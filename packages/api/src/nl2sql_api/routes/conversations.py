"""The `conversations` namespace of `/api/v1`: chats, their turns, chat tools and the report.

A thin layer over `engine.conversations` and `engine.chat_tools`: each handler passes the caller as `user_context=` and
serves the facade's own models. The model decides what a turn does, so there is one `turns` route; a turn that
needs a clarification is answered by a `choice` in the next one.
"""
import contextvars
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeout
from typing import Annotated, Iterator, List, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from nl2sql import NL2SQL, ChatToolInfo, Conversation, ConversationInfo, Event, LLMOverride, Turn, UserContext
from nl2sql_api import identity, sse
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE
from nl2sql_api.models.conversations import RetryRequest, StartRequest, ToolRequest, TurnRequest
from nl2sql_api.overrides import llm_override

router = APIRouter(tags=["conversations"])

Caller = Annotated[UserContext, Depends(identity.caller)]
Engine = Annotated[NL2SQL, Depends(get_engine)]
Override = Annotated[Optional[LLMOverride], Depends(llm_override)]

_NOT_FOUND = {404: {**ERROR_RESPONSE, "description": "No such conversation for this caller."}}
_RETRY_NOT_FOUND = {404: {**ERROR_RESPONSE, "description": "No such conversation for this caller, or an id in "
                                                           "`approval_ids` is unknown, waiting, denied or expired."}}
_REFUSED = {422: {**ERROR_RESPONSE, "description": "The request does not validate, or the facade refused an argument."}}
_BUSY = {409: {**ERROR_RESPONSE, "description": "The conversation is already running a turn."}}
_TURN = {**identity.AUTH_RESPONSES, **_NOT_FOUND, **_REFUSED, **_BUSY}

# A stream's refusals that only the run can raise (a busy conversation) show on its first event; this long is waited for
# one so they answer with their status and the envelope instead of cutting a stream that has begun.
SETTLE_S = 0.5
_END = object()


class _Settled:
    """The events of a turn, started and given ``settle_s`` to refuse before the response begins.

    ``ConversationBusy`` comes from the first ``next`` (the lock is taken in the run), so it is read here: raised
    now if it came inside the wait, else the stream goes ahead and the first event is handed over when it arrives.
    One lock keeps the first pull, the later ones and ``close`` from entering the generator together.
    """

    def __init__(self, events: Iterator[Event], settle_s: float) -> None:
        self._events = events
        self._lock = threading.Lock()
        self._first: Future = Future()
        self._handed = False
        threading.Thread(target=contextvars.copy_context().run, args=(self._pull,), name="nl2sql-first-event",
                         daemon=True).start()
        try:
            self._first.result(timeout=settle_s)
        except FutureTimeout:
            pass

    def _pull(self) -> None:
        try:
            with self._lock:
                item = next(self._events, _END)
            self._first.set_result(item)
        except BaseException as exc:
            self._first.set_exception(exc)

    def __iter__(self) -> "_Settled":
        return self

    def __next__(self) -> Event:
        if not self._handed:
            self._handed = True
            item = self._first.result()
            if item is _END:
                raise StopIteration
            return item
        with self._lock:
            return next(self._events)

    def close(self) -> None:
        """Closes the run, once the step it is inside of returns (a running generator cannot be closed)."""
        with self._lock:
            close = getattr(self._events, "close", None)
            if close is not None:
                close()


@router.post("/conversations", response_model=Conversation, status_code=201, summary="Start a conversation",
             responses={**identity.AUTH_RESPONSES, **_REFUSED, 404: {**ERROR_RESPONSE, "description":
                                                                     "`dashboard_id` is not the caller's dashboard."}})
def start_conversation(body: StartRequest, user: Caller, engine: Engine):
    """A new, empty conversation owned by the caller. Nothing runs. With `dashboard_id` the chat edits that dashboard."""
    return engine.conversations.start(user_context=user, datasource_id=body.datasource_id,
                                      dashboard_id=body.dashboard_id)


@router.get("/conversations", response_model=List[ConversationInfo], summary="List conversations",
            responses=identity.AUTH_RESPONSES)
def list_conversations(user: Caller, engine: Engine, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    """The caller's conversations, newest first: id, title, last update and the number of turns."""
    return engine.conversations.list(user_context=user, limit=limit)


@router.get("/conversations/{conversation_id}", response_model=Conversation, summary="Read a conversation",
            responses={**identity.AUTH_RESPONSES, **_NOT_FOUND})
def get_conversation(conversation_id: str, user: Caller, engine: Engine):
    """The conversation with its stored turns and, when the last turn asked one, the pending clarification. Another
    user's conversation is a 404."""
    return engine.conversations.get(conversation_id, user_context=user)


@router.delete("/conversations/{conversation_id}", status_code=204, response_class=Response,
               summary="Delete a conversation", responses={**identity.AUTH_RESPONSES, **_NOT_FOUND, **_BUSY})
def delete_conversation(conversation_id: str, user: Caller, engine: Engine):
    """Deletes the conversation and its turns; its results stay until their own retention."""
    engine.conversations.delete(conversation_id, user_context=user)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/turns", response_model=Turn, summary="Send a message",
             responses=_TURN)
def post_turn(conversation_id: str, body: TurnRequest, user: Caller, engine: Engine, override: Override):
    """One turn: the model's bounded loop of tool calls and a reply, stored. A pipeline outcome is not an error: the
    turn's `status` is `answered`, `needs_clarification` (answer it with `choice` in the next turn), `needs_approval`,
    `stopped` or `error`. Keys and models in the `X-NL2SQL-*` headers apply to the whole turn when `api.request_llm`
    is on."""
    return engine.conversations.ask(conversation_id, body.text, user_context=user, choice=body.choice,
                                    execute=body.execute, llm_override=override)


@router.post(
    "/conversations/{conversation_id}/turns/stream",
    summary="Send a message and follow it",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}},
                     "description": "Server-Sent Events: the run events of each question the model asks, a "
                                    "`conversation.step` after each step, and `conversation.turn` last, whose "
                                    "`data.stored` is the stored `Turn`. A `: keep-alive` comment every 15 seconds. "
                                    "Closing the connection cancels the turn and stores nothing. A failure after the "
                                    "stream has begun is a last `run.failed` event."},
               **_TURN},
)
def post_turn_stream(conversation_id: str, body: TurnRequest, request: Request, user: Caller, engine: Engine,
                     override: Override):
    """As `POST /turns`, as events. A refusal (not the caller's, a bad body, a busy conversation) answers with the
    error envelope before the stream begins; once it has, a failure is a last `run.failed` event (its `error_code`, or
    `UNKNOWN_ERROR` with a fixed message)."""
    events = engine.conversations.stream(conversation_id, body.text, user_context=user, choice=body.choice,
                                         execute=body.execute, llm_override=override)
    settled = _Settled(events, SETTLE_S)
    return sse.event_stream(lambda: settled, request, heartbeat_s=sse.HEARTBEAT_S)


@router.post("/conversations/{conversation_id}/retry", response_model=Turn, summary="Run the last question again",
             responses={**_TURN, **_RETRY_NOT_FOUND})
def retry_turn(conversation_id: str, user: Caller, engine: Engine, override: Override,
               body: Optional[RetryRequest] = None):
    """Runs the conversation's last question again as a turn of kind `retry`, with no model call of its own. After a
    `needs_approval` turn, send the granted request ids as `approval_ids`."""
    return engine.conversations.retry(conversation_id, user_context=user,
                                      approvals=(body or RetryRequest()).approval_ids, llm_override=override)


@router.post("/conversations/{conversation_id}/tools/{name}", response_model=Turn, summary="Run a chat tool",
             responses={**identity.AUTH_RESPONSES, **_NOT_FOUND, **_REFUSED, **_BUSY})
def run_tool(conversation_id: str, name: str, user: Caller, engine: Engine, body: Optional[ToolRequest] = None):
    """One chat tool by name, as a turn of kind `tool` with no model call: how a client runs a tool the model offered.
    Arguments that do not validate are a 422 and store nothing; a tool that is not enabled is a stored error step."""
    return engine.conversations.tool(conversation_id, name, (body or ToolRequest()).args, user_context=user)


@router.get("/conversations/{conversation_id}/report", response_class=Response, summary="The conversation as a report",
            responses={200: {"content": {"text/markdown": {}}, "description": "The report, as Markdown."},
                       **identity.AUTH_RESPONSES, **_NOT_FOUND, **_REFUSED})
def get_report(conversation_id: str, user: Caller, engine: Engine,
               turns: Annotated[Optional[List[int]], Query(description="Turn numbers to include; all by default.")] = None,
               title: Optional[str] = None):
    """The answered questions of the conversation written up as Markdown, with no model call. A turn that does not
    exist, or a conversation with no answered question, is a 422."""
    text = engine.conversations.report(conversation_id, user_context=user, turns=turns, title=title)
    return Response(text, media_type="text/markdown")


@router.get("/chat-tools", response_model=List[ChatToolInfo], summary="List chat tools",
            responses=identity.AUTH_RESPONSES)
def list_chat_tools(user: Caller, engine: Engine):
    """The enabled chat tools: name, description and the JSON Schema of their arguments. `ask` is the
    conversation's own and is not listed."""
    return engine.chat_tools.list()
