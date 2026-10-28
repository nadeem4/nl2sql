"""The conversations service behind ``engine.conversations`` (docs/architecture/wave_c.md, section 3).

A turn is a bounded loop. The model chooses, step by step, a reply or the ``ask`` tool; an ``ask`` step is a
run through ``run_query`` with its own scope. The turn has a scope of its own for the loop's model calls: it
publishes no ``run.started`` and closes once, with ``conversation.turn``. Nothing is stored unless the turn
finishes.
"""
from __future__ import annotations

import contextvars
import queue
import secrets
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence

from nl2sql.api.query_api import QueryAPI, QueryResult
from nl2sql.api.results_api import ResultsAPI
from nl2sql.clarify.models import ClarificationChoice, ClarificationRequest
from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.context import current_conversation_id
from nl2sql.common.errors import ConversationBusy, DashboardNotFound, ErrorCode
from nl2sql.conversations.agent import ConversationAgent, block, check_call, checked_step, ASK_SPEC, AskArgs
from nl2sql.conversations.models import Conversation, ConversationInfo, ToolCall, ToolStep, Turn, TurnKind, TurnStatus
from nl2sql.conversations.report import build_report
from nl2sql.conversations.store import ConversationStore, owner_of, strip_rows
from nl2sql.conversations.tools import ChatToolRunner
from nl2sql.events import STREAM_EVENTS, RunScope
from nl2sql.events.catalog import ChatToolCalled, ConversationStarted, ConversationStep, ConversationTurn
from nl2sql.hooks.approval import Approval
from nl2sql.limits import Budget, LimitReached
from nl2sql.llm.failures import classify_provider_error
from nl2sql.llm.wires import StructuredOutputError
from nl2sql.pipeline.runtime import usage_prices
from nl2sql.policies import UserContext
from nl2sql.services.callbacks.token_handler import TokenUsageCallback
from nl2sql_sdk.events import Event

_STOPPING = ("needs_clarification", "needs_approval", "stopped")
_INVALID_REPLY = "I could not tell what to do with that. Please ask it as a full question."


@dataclass
class _Run:
    """One turn in flight."""

    conversation: Conversation
    user: UserContext
    kind: TurnKind
    text: str
    execute: bool
    token: CancellationToken
    scope: RunScope
    # Called with each ask step's scope as it opens (the stream follows it).
    on_step_scope: Optional[Callable[[RunScope], None]] = None
    # A cancelled turn is stored as an error (a call to ``ask``) or dropped (a closed stream).
    keep_cancelled: bool = True
    steps: List[ToolStep] = field(default_factory=list)
    results: List[QueryResult] = field(default_factory=list)  # the steps' results, with their rows
    closing: Optional[Event] = None  # the published conversation.turn


def _status(result: QueryResult) -> str:
    return result.status if result.status in _STOPPING + ("error",) else "ok"


def _step_error(result: QueryResult) -> Optional[Dict[str, Any]]:
    if result.status != "error":
        return None
    first = result.errors[0] if result.errors else {}
    return {"code": first.get("error_code") or "UNKNOWN_ERROR", "message": first.get("message") or ""}


class Conversations:
    """What ``engine.conversations`` does. ``ConversationsAPI`` is its public face."""

    def __init__(self, ctx: Any, engine: Any) -> None:
        self._ctx = ctx
        self._store = ConversationStore(ctx.state)
        self._queries = QueryAPI(ctx)
        self._agent = ConversationAgent(ctx)
        self._chat = ChatToolRunner(ctx.chat_tools, engine)  # a tool is handed the public facade, never ctx

    @property
    def _settings(self):
        return self._ctx.config.conversations

    def _owner(self, user: UserContext):
        return owner_of(user, self._ctx.tenant_id)

    # ------------------------------------------------------------ the plain methods

    def start(self, *, user_context: UserContext, datasource_id: Optional[str] = None,
              dashboard_id: Optional[str] = None) -> Conversation:
        owner = self._owner(user_context)
        if dashboard_id is not None:
            doc = self._ctx.state.get_dashboard(dashboard_id)
            if doc is None or (doc.get("tenant_id"), doc.get("user_id")) != owner:
                raise DashboardNotFound(f"No dashboard '{dashboard_id}' for this caller.")
        now = datetime.now(timezone.utc)
        conversation = Conversation(id=secrets.token_urlsafe(16), tenant_id=owner[0], user_id=owner[1],
                                    title="New conversation", datasource_id=datasource_id,
                                    dashboard_id=dashboard_id, created_at=now, updated_at=now)
        self._store.put(conversation)
        self._ctx.bus.publish("conversation.started", ConversationStarted(
            conversation_id=conversation.id, user_id=owner[1], datasource_id=datasource_id), tenant_id=owner[0])
        return conversation

    def get(self, conversation_id: str, *, user_context: UserContext) -> Conversation:
        return self._store.get(conversation_id, self._owner(user_context))

    def list(self, *, user_context: UserContext, limit: int = 20) -> List[ConversationInfo]:
        return self._store.list(self._owner(user_context), limit)

    def delete(self, conversation_id: str, *, user_context: UserContext) -> None:
        self.get(conversation_id, user_context=user_context)
        with self._locked(conversation_id):  # a running turn would store the conversation again
            self._store.delete(conversation_id)

    # ------------------------------------------------------------ turns

    def ask(self, conversation_id: str, text: Optional[str] = None, *, user_context: UserContext,
            choice: Optional[str] = None, execute: bool = True,
            cancellation_token: Optional[CancellationToken] = None) -> Turn:
        kind, shown = self._prepare(self.get(conversation_id, user_context=user_context), text, choice)
        body = self._by_choice if kind == "choice" else self._by_model
        return self._turn(conversation_id, user_context, kind, shown, execute,
                          cancellation_token or CancellationToken(), lambda run: body(run, choice))

    def retry(self, conversation_id: str, *, user_context: UserContext,
              approvals: Sequence[Approval] = ()) -> Turn:
        conversation = self.get(conversation_id, user_context=user_context)
        question = next((s.question for t in reversed(conversation.turns) for s in reversed(t.steps)
                         if s.call.name == "ask" and s.question), None)
        if question is None:
            raise ValueError("This conversation has no question to run again.")

        def body(run: _Run) -> Turn:
            self._ask(run, ToolCall(name="ask", args={"question": question}), approvals=approvals)
            return self._finish(run)

        return self._turn(conversation_id, user_context, "retry", question, True, CancellationToken(), body)

    def tool(self, conversation_id: str, name: str, args: Optional[Dict[str, Any]] = None, *,
             user_context: UserContext) -> Turn:
        """One registry chat tool, by name, as a turn of kind ``tool`` with no model call. Arguments that do not
        validate raise ``ValueError`` and store nothing; a tool that is not enabled is a stored error step."""
        call = ToolCall(name=name, args=dict(args or {}))
        conversation = self.get(conversation_id, user_context=user_context)
        if name in self._chat.names:
            check_call(call, self._chat.args_models(), [], offered=False)
        self._check_length(conversation)

        def body(run: _Run) -> Turn:
            self._tool(run, call)
            return self._finish(run)

        return self._turn(conversation_id, user_context, "tool", name, True, CancellationToken(), body)

    def report(self, conversation_id: str, *, user_context: UserContext, turns: Optional[Sequence[int]] = None,
               title: Optional[str] = None) -> str:
        """The Markdown report of the stored turns, with no model call."""
        return build_report(self.get(conversation_id, user_context=user_context), ResultsAPI(self._ctx),
                            turns=turns, title=title)

    def ask_only(self, conversation_id: str, question: str, *, user_context: UserContext,
                 choice: Optional[str] = None) -> QueryResult:
        conversation = self.get(conversation_id, user_context=user_context)
        if choice is not None:
            self._pending_option(conversation, choice)
        holder: List[QueryResult] = []

        def body(run: _Run) -> Turn:
            self._ask(run, ToolCall(name="ask", args={"question": question, **({"choice": choice} if choice else {})}))
            holder.append(run.results[-1])
            return self._finish(run)

        self._turn(conversation_id, user_context, "tool", question, True, CancellationToken(), body)
        return holder[0]

    def stream(self, conversation_id: str, text: Optional[str] = None, *, user_context: UserContext,
               choice: Optional[str] = None, execute: bool = True) -> Iterator[Event]:
        """As ``ask``, as events. Ownership and the arguments are checked now; a busy conversation raises
        ``ConversationBusy`` from the first ``next``."""
        kind, shown = self._prepare(self.get(conversation_id, user_context=user_context), text, choice)
        return self._stream(conversation_id, user_context, kind, shown, execute, choice)

    # ------------------------------------------------------------ the machinery

    def _prepare(self, conversation: Conversation, text: Optional[str], choice: Optional[str]):
        """``(kind, text shown)`` of the turn asked for, or the ``ValueError`` that refuses it."""
        if (text is None) == (choice is None):
            raise ValueError("Pass the message as text or a choice, one of the two.")
        if choice is not None:
            shown = self._pending_option(conversation, choice).label
        elif not text.strip():
            raise ValueError("Say what you want to ask.")
        else:
            shown = text
        self._check_length(conversation)
        return ("choice" if choice is not None else "message"), shown

    def _check_length(self, conversation: Conversation) -> None:
        if len(conversation.turns) >= self._settings.max_turns:
            raise ValueError(f"{ErrorCode.CONVERSATION_TOO_LONG.value}: this conversation has "
                             f"{self._settings.max_turns} turns. Start a new conversation.")

    @staticmethod
    def _pending_option(conversation: Conversation, choice: str):
        pending = conversation.pending
        if pending is None:
            raise ValueError("Nothing in this conversation is waiting for a choice.")
        option = next((o for o in pending.options if o.id == choice), None)
        if option is None:
            raise ValueError(f"'{choice}' is not an option of the pending question: "
                             f"{', '.join(o.id for o in pending.options)}.")
        return option

    @contextmanager
    def _locked(self, conversation_id: str) -> Iterator[None]:
        ttl = int(self._ctx.settings.pipeline.timeout_s * self._settings.max_tool_calls) + 10
        with ExitStack() as stack:
            try:
                stack.enter_context(self._ctx.state.lock(f"conversation:{conversation_id}", ttl))
            except RuntimeError as exc:
                raise ConversationBusy(f"Conversation '{conversation_id}' is running a turn. {exc}") from exc
            yield

    def _open(self, conversation: Conversation, user: UserContext, kind: TurnKind, text: str, execute: bool,
              token: CancellationToken, **options: Any) -> _Run:
        """A turn's run; call it under the conversation's lock, with the conversation read under it."""
        self._check_length(conversation)
        usage = TokenUsageCallback(prices=usage_prices(self._ctx))
        scope = RunScope(self._ctx.bus, question=text, user_context=user, datasource_id=conversation.datasource_id,
                         execute=execute, tenant_id=conversation.tenant_id, usage=usage)
        budget = Budget.for_question(self._ctx.settings.pipeline, usage)
        if budget is not None:
            scope.attach(budget)
        return _Run(conversation, user, kind, text, execute, token, scope, **options)

    def _turn(self, conversation_id: str, user: UserContext, kind: TurnKind, text: str, execute: bool,
              token: CancellationToken, body: Callable[[_Run], Turn], **options: Any) -> Turn:
        with self._locked(conversation_id):
            run = self._open(self.get(conversation_id, user_context=user), user, kind, text, execute, token,
                             **options)
            return self._guarded(run, body)

    def _guarded(self, run: _Run, body: Callable[[_Run], Turn]) -> Turn:
        """``body(run)``, with the turn's scope closed whatever happens: by ``_finish``, else as failed."""
        try:
            return body(run)
        except BaseException as exc:
            code = ErrorCode.CANCELLED if isinstance(exc, _Dropped) else ErrorCode.UNKNOWN_ERROR
            run.scope.fail(f"The turn failed: {exc or type(exc).__name__}", code)
            raise

    def _by_choice(self, run: _Run, option_id: str) -> Turn:
        self._pending_option(run.conversation, option_id)  # read under the lock: it may have gone since _prepare
        pending = run.conversation.pending
        self._ask(run, ToolCall(name="ask", args={"question": pending.question, "choice": option_id}))
        return self._finish(run)

    def _by_model(self, run: _Run, _choice: Optional[str] = None) -> Turn:
        settings, chat = self._settings, self._chat
        pending = run.conversation.pending
        option_ids = [o.id for o in pending.options] if pending else []
        reply, offers, error, status = None, [], None, None
        try:
            for used in range(settings.max_tool_calls + 1):
                self._check_cancelled(run)
                if run.token.is_cancelled():
                    break  # the step in flight was the last; _finish stores the turn as cancelled
                left = settings.max_tool_calls - used
                message = block(run.conversation.turns, run.text, context_turns=settings.context_turns,
                                pending=pending, tools=[ASK_SPEC, *chat.specs()], calls_left=left,
                                this_turn=run.steps)
                with run.scope.entered():
                    step = self._agent.decide(message, checked_step(chat.args_models(), option_ids, left),
                                              run.scope.callbacks())
                if step.reply is not None:
                    reply, offers = step.reply, step.offers
                    break
                if step.call.name != "ask":
                    self._tool(run, step.call)
                elif self._ask(run, step.call).status in _STOPPING:
                    break
        except StructuredOutputError as exc:
            reply, status = _INVALID_REPLY, "error"
            error = {"code": ErrorCode.MODEL_STEP_INVALID.value, "message": str(exc)}
        except LimitReached as exc:
            status = "stopped"
            error = {"code": ErrorCode.RUN_LIMIT_REACHED.value, "message": str(exc)}
        except Exception as exc:
            failure = classify_provider_error(exc)
            if failure is None:
                raise
            status = "error"
            error = {"code": failure.code.value, "message": failure.message}
        return self._finish(run, reply=reply, offers=offers, error=error, status=status)

    @staticmethod
    def _check_cancelled(run: _Run) -> None:
        if run.token.is_cancelled() and not run.keep_cancelled:
            raise _Dropped("The stream was closed.")

    def _ask(self, run: _Run, call: ToolCall, *, approvals: Sequence[Approval] = ()) -> ToolStep:
        """One ``ask`` step: a run through ``run_query`` as the caller, recorded and announced."""
        conversation = run.conversation
        args = AskArgs.model_validate(call.args)
        choice = None
        question = args.question
        if args.choice is not None:
            self._pending_option(conversation, args.choice)  # still pending now that the lock is held
            pending = conversation.pending
            choice = ClarificationChoice(request=pending, option_id=args.choice)
            question = pending.question  # Wave B refuses a choice unless the question is the request's own
        queries = self._queries
        scope = queries.open_scope(question, datasource_id=conversation.datasource_id, execute=run.execute,
                                   user_context=run.user)
        if run.on_step_scope:
            run.on_step_scope(scope)
        marker = current_conversation_id.set(conversation.id)
        try:
            result = queries.run_query(question, datasource_id=conversation.datasource_id, execute=run.execute,
                                       user_context=run.user, cancellation_token=run.token, scope=scope,
                                       approvals=list(approvals) or None, choice=choice)
        except BaseException as exc:
            if not scope.closed:
                scope.fail(f"The run failed: {exc or type(exc).__name__}", ErrorCode.UNKNOWN_ERROR)
            raise
        finally:
            current_conversation_id.reset(marker)
        step = ToolStep(call=call, status=_status(result), question=question, trace_id=result.trace_id,
                        result=strip_rows(result), error=_step_error(result))
        run.steps.append(step)
        run.results.append(result)
        run.scope.publish("conversation.step", ConversationStep(
            conversation_id=conversation.id, turn=len(conversation.turns) + 1, tool=call.name,
            status=step.status, trace_id=step.trace_id, args=sorted(call.args)))
        return step

    def _tool(self, run: _Run, call: ToolCall) -> ToolStep:
        """One registry chat-tool step: the tool sees the conversation with this turn's steps so far, makes no
        model call, and whatever it does is a step the model can read."""
        conversation = run.conversation
        index = len(conversation.turns) + 1
        now = datetime.now(timezone.utc)
        in_flight = Turn(index=index, at=now, kind=run.kind, text=run.text, steps=list(run.steps),
                         status="answered", trace_id=run.scope.trace_id)
        began = time.monotonic()
        step = self._chat.run(conversation.model_copy(update={"turns": [*conversation.turns, in_flight]}),
                              run.user, call)
        run.steps.append(step)
        run.scope.publish("chat_tool.called", ChatToolCalled(
            tool=call.name, conversation_id=conversation.id, turn=index, ok=step.status == "ok",
            seconds=round(time.monotonic() - began, 3), error_code=(step.error or {}).get("code")))
        run.scope.publish("conversation.step", ConversationStep(
            conversation_id=conversation.id, turn=index, tool=call.name, status=step.status,
            args=sorted(call.args)))
        return step

    def _finish(self, run: _Run, *, reply: Optional[str] = None, offers: Sequence[ToolCall] = (),
                error: Optional[Dict[str, Any]] = None, status: Optional[str] = None) -> Turn:
        """Store the turn, then close its scope with ``conversation.turn``."""
        if run.token.is_cancelled():
            if not run.keep_cancelled:
                raise _Dropped("The stream was closed.")
            if error is None and not any(s.status in _STOPPING for s in run.steps):
                error = {"code": ErrorCode.CANCELLED.value, "message": "The turn was cancelled."}
                status = "error"
        conversation = run.conversation
        last = next((s.status for s in reversed(run.steps) if s.status != "ok"), "answered")
        now = datetime.now(timezone.utc)
        turn = Turn(index=len(conversation.turns) + 1, at=now, kind=run.kind, text=run.text, reply=reply,
                    offers=list(offers), steps=run.steps, status=status or last, trace_id=run.scope.trace_id,
                    usage=run.scope.usage.usage(), error=error)
        if not conversation.turns:
            conversation.title = run.text[:80]
        conversation.turns.append(turn)
        conversation.updated_at = now
        clarifying = next((s.result.clarification for s in run.steps
                           if s.status == "needs_clarification" and s.result), None)
        conversation.pending = clarifying
        self._store.put(conversation)
        run.closing = run.scope.end("conversation.turn", ConversationTurn(
            conversation_id=conversation.id, turn=turn.index, kind=turn.kind, status=turn.status,
            trace_id=turn.trace_id, steps=len(turn.steps)))
        return turn

    # ------------------------------------------------------------ the stream

    def _stream(self, conversation_id: str, user: UserContext, kind: TurnKind, text: str, execute: bool,
                choice: Optional[str]) -> Iterator[Event]:
        events: "queue.Queue[Any]" = queue.Queue()
        token = CancellationToken()
        body = (lambda run: self._by_choice(run, choice)) if kind == "choice" else self._by_model

        def follow(scope: RunScope) -> None:
            scope.follow(events.put, STREAM_EVENTS)

        def work() -> None:
            try:
                with self._locked(conversation_id):
                    run = self._open(self.get(conversation_id, user_context=user), user, kind, text, execute,
                                     token, on_step_scope=follow, keep_cancelled=False)
                    run.scope.follow(events.put, {"conversation.step", "run.failed"})
                    try:
                        turn = self._guarded(run, body)
                    except _Dropped:
                        return
                    events.put(run.closing.model_copy(update={"data": {
                        **run.closing.data, "stored": turn.model_dump(mode="json")}}))
            except BaseException as exc:
                events.put(exc)
            finally:
                events.put(None)

        threading.Thread(target=contextvars.copy_context().run, args=(work,), name="nl2sql-conversation",
                         daemon=True).start()
        finished = False
        try:
            while (item := events.get()) is not None:
                if isinstance(item, BaseException):
                    finished = True
                    raise item
                yield item
            finished = True
        finally:
            if not finished:
                token.cancel()


class _Dropped(Exception):
    """A closed stream's turn: nothing is stored."""
