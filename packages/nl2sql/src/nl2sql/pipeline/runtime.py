from __future__ import annotations

import concurrent.futures
import contextlib
import contextvars
import datetime as _dt
import signal
import sys
import threading
import time
import traceback
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence

from langchain_core.callbacks import BaseCallbackHandler

from nl2sql.policies import UserContext
from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.exceptions import PipelineExecutionError
from nl2sql.common.dates import run_date
from nl2sql.runtime import Runtime
from nl2sql.hooks import hooks_of
from nl2sql.limits import Budget, RunStop, describe, stop_answer
from nl2sql.llm.failures import provider_error
from nl2sql.pipeline.graph import build_graph
from nl2sql.pipeline.nodes.curated_matcher import curated_answers_on
from nl2sql.pipeline.pinned import PinnedRun, check_pinned, pinned_from_approvals
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.timing import NodeTimingCallback
from nl2sql.services.callbacks.token_handler import TokenUsageCallback
from nl2sql.tracing.recorder import TraceRecorder
from nl2sql.tracing.trace import write_run_trace

if TYPE_CHECKING:
    from nl2sql.events import RunScope
    from nl2sql.hooks.approval import Approval

# What a caller is told when the run ran out of time. It is returned in the
# answer synthesizer's own shape, which is the only place `result_from_state`
# looks for an answer.
TIMEOUT_ANSWER = "I apologize, but the request timed out. Please try again with a simpler query."


class RunCancelled(Exception):
    """Raised inside the graph once its run's token is cancelled."""


class _StopWhenCancelled(BaseCallbackHandler):
    """Refuses to start another step, or another model call, after a cancel.

    Nodes only see the token where they read it (the SQL agent's routers), so
    without this a run cancelled during the decomposer would still call the
    planner and the answer synthesizer. ``raise_error`` makes the callback
    manager re-raise instead of logging, which stops the graph at the next
    node and a model call before its request is sent. The step already in
    flight -- one model call at most -- finishes.
    """

    raise_error = True

    def __init__(self, token: CancellationToken) -> None:
        self._token = token

    def _check(self, *args, **kwargs) -> None:
        if self._token.is_cancelled():
            raise RunCancelled("Pipeline cancelled by user.")

    on_chain_start = _check
    on_llm_start = _check
    on_chat_model_start = _check


def _start_keyboard_cancel_listener(
    token: CancellationToken,
    done: threading.Event,
) -> None:
    """Cancel ``token`` when Ctrl+X is pressed, until ``done`` is set.

    One listener per run: the thread exits once the run finishes, so a later run
    starts its own listener bound to its own token.
    """
    if sys.platform != "win32":
        return

    if not sys.stdin or not sys.stdin.isatty():
        return

    try:
        import msvcrt
    except Exception:
        return

    def _listen():
        while not done.is_set():
            if msvcrt.kbhit():
                char = msvcrt.getch()
                if char == b"\x18":  # Ctrl+X
                    token.cancel()
                    return
            done.wait(0.05)

    threading.Thread(target=_listen, daemon=True).start()


def _install_signal_handlers(token: CancellationToken) -> Callable[[], None]:
    """Install SIGINT/SIGTERM cancellation handlers, returning an undo callable.

    ``signal.signal`` raises ``ValueError`` anywhere but the main thread, and a
    server embedding the engine runs it off that thread: FastAPI dispatches the
    synchronous ``/query`` handler into Starlette's threadpool. Interpreter-wide
    signal handlers are not ours to install from there anyway, so a worker thread
    gets a no-op instead of a crash; it still cancels through its own token.
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    previous = {}

    def _handler(signum, frame):
        token.cancel()

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, _handler)

    def _restore():
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    return _restore


class _NoLLMs:
    """The run handle for a context without an LLM registry (a test's stand-in runtime)."""

    def unpin(self) -> None:
        pass

    def release(self) -> None:
        pass

    def end(self) -> None:
        pass


def _begin_run(ctx: Runtime):
    """Pins the engine's current LLM set to this run (``LLMRegistry.begin_run``)."""
    llms = getattr(ctx, "llm_registry", None)
    return llms.begin_run() if llms is not None else _NoLLMs()


def _pin_index(ctx: Runtime):
    """A runtime reading the index through the pointers as they are now (``Runtime.pin_index``)."""
    pin = getattr(ctx, "pin_index", None)
    return pin() if callable(pin) else ctx


def usage_prices(ctx: Runtime) -> Dict:
    """``llm.prices``: the overrides a run's ``TokenUsageCallback`` prices its calls with, before the dated table."""
    return dict(ctx.config.llm.prices)


def run_with_graph(
    ctx: Runtime,
    user_query: str,
    datasource_id: Optional[str] = None,
    execute: bool = True,
    callbacks: Optional[List] = None,
    user_context: Optional[UserContext] = None,
    cancellation_token: Optional[CancellationToken] = None,
    trace_mode: Optional[str] = None,
    stream_answer: bool = False,
    scope: Optional["RunScope"] = None,
    pinned: Optional[PinnedRun] = None,
    approvals: Optional[Sequence["Approval"]] = None,
    clarify: bool = True,
    answer: bool = True,
) -> Dict:
    """Convenience function to run the full pipeline.

    Every run is recorded by a ``TraceRecorder``; ``TRACE_MODE`` (or
    ``trace_mode``, which wins) decides whether the trace is written. When it
    is, the returned state carries ``trace_path``. ``cancellation_token`` lets
    a caller stop the run (trace replay does, on divergence).
    ``stream_answer`` asks the answer model to stream its answer, so a
    callback's ``on_llm_new_token`` sees it as it is written
    (``stream_query`` passes it with a :class:`~nl2sql.pipeline.events.RunEvents`).
    ``scope`` is the run's :class:`~nl2sql.events.RunScope`, opened and closed
    by the caller: the run takes its ``trace_id``, enters its context in the
    worker (so the run's events and log lines carry it, on LangGraph's
    threads too) and adds its ``callbacks()``: its usage collector, when it
    has one, is the run's.

    ``pinned`` is a decomposition and plans the run takes as given (a
    supplied plan; Wave B's chosen one): the run enters at the pinned entry.
    ``approvals`` are passed to the ``execute.before`` hooks; without
    ``pinned``, the first one that is this run's (unedited, its SQL its hash,
    the same question after ``run.before`` and asker) and whose approver
    holds one of its hook's approver roles pins what it approved, as
    ``approved`` (``pinned_from_approvals``). ``check_pinned`` refuses a
    chosen plan with approvals (a clarification re-ask is a new question,
    not an approved one) and a ``datasource_id`` that is not the pin's.

    ``clarify`` off runs the default reading where the planner saw two
    (``settings.clarify.enabled`` must be on too for the run to ask); the
    prompts are the same either way (docs/architecture/wave_b.md, D58).

    ``answer`` off builds the graph without the answer synthesizer: the run
    ends at the aggregator, ``final_answer`` is None and no model call is made
    for prose (docs/architecture/wave_c.md, D119).
    """
    approvals = list(approvals or [])
    # QueryAPI checks this first, before the run's scope opens; a direct caller is held to it here.
    check_pinned(pinned, approvals, datasource_id, execute=execute, curated=curated_answers_on(ctx))
    token = cancellation_token or CancellationToken()
    run_done = threading.Event()
    restore_signals = _install_signal_handlers(token)
    _start_keyboard_cancel_listener(token, run_done)

    # The run reads the engine's LLM set and index pointers once, here: every
    # node is built from them, and a swap or a rebuild while the run is in
    # flight leaves the run on what it started with.
    ctx = _pin_index(ctx)
    run_llms = _begin_run(ctx)
    def _graph(pin: Optional[PinnedRun]):
        # A pinned decomposition has a graph of its own; every other run's is the usual one.
        # Only a run that differs from the usual one says so.
        return build_graph(ctx, execute=execute, **({"pinned": True} if pin is not None and pin.enters_pinned
                                                     else {}), **({} if answer else {"answer": False}))

    try:
        graph = _graph(pinned)
    except BaseException:
        run_llms.end()
        raise

    # No role is an empty UserContext, the state's own default: policies then see
    # a caller with no roles, as it does for one whose roles list is empty.
    initial_state = GraphState(
        user_query=user_query,
        user_context=user_context or UserContext(),
        datasource_id=datasource_id,
        clarify=clarify,
        **({"trace_id": scope.trace_id} if scope is not None else {}),
    )

    settings = ctx.settings
    timeout_sec = settings.pipeline.timeout_s
    timing = NodeTimingCallback()
    # The scope's collector, when it has one, so a model call made in the run
    # before the graph (Wave C's rewrite) counts in the run's usage.
    usage = (scope.usage if scope is not None and scope.usage is not None
             else TokenUsageCallback(prices=usage_prices(ctx)))
    recorder = TraceRecorder()
    started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    # The date the prompts were told; a trace replay pins it, or every planner
    # prompt recorded on another day would differ from the replayed one.
    today = run_date(settings.pipeline.today).isoformat()
    t0 = time.perf_counter()

    def _telemetry() -> Dict:
        """Timings and token usage so far; also reported for a timed-out or cancelled run."""
        return {"timings": dict(timing.timings), "usage": usage.usage().model_dump(mode="json")}

    def _done(out: Dict, outcome: str, error: Optional[str] = None) -> Dict:
        """Stamps the trace id and, if the trace was written, where it went."""
        out.setdefault("trace_id", initial_state.trace_id)
        path = write_run_trace(
            trace_mode or settings.traces.mode,
            trace_id=initial_state.trace_id,
            request={
                "question": user_query,
                "roles": list(getattr(initial_state.user_context, "roles", []) or []),
                "tenant_id": getattr(initial_state.user_context, "tenant_id", None),
                "datasource_id": datasource_id,
                "execute": execute,
                "clarify": clarify,
                "answer": answer,
                "today": today,
            },
            started_at=started_at,
            finished_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            duration_s=round(time.perf_counter() - t0, 4),
            outcome=outcome,
            recorder=recorder,
            usage=usage,
            ctx=ctx,
            state=out,
            error=error,
            events=list(scope.events) if scope is not None else [],
        )
        if path:
            out["trace_path"] = path
        # The runtime the run pinned (its knowledge and index pointers), so the result is explained from the
        # glossary and snapshot the prompts used, even if a reload swapped them meanwhile
        # (nl2sql.knowledge.scope.caller_knowledge). Not a state field: nothing stores or sends it.
        out["pinned_runtime"] = ctx
        return out

    def _stopped(stop: RunStop) -> Dict:
        """A run a ``Budget`` stopped at a limit: ``stopped``, with the limit and the setting named."""
        message = f"The question reached its {describe(stop)}."
        text = stop_answer(stop)
        return _done({
            "errors": [
                PipelineError(
                    node="orchestrator",
                    message=message,
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.RUN_LIMIT_REACHED,
                )
            ],
            "stop": stop,
            # Where the answer synthesizer would have written it, as the timeout's answer is;
            # a run that asked for no answer (answer=False) has none.
            **({"answer_synthesizer_response": {"final_answer": {
                "summary": text, "format_type": "text", "content": text, "warnings": []}}} if answer else {}),
            **_telemetry(),
        }, "stopped", message)

    def _cancelled() -> Dict:
        if isinstance(token.reason, RunStop):
            return _stopped(token.reason)
        return _done({
            "errors": [
                PipelineError(
                    node="orchestrator",
                    message="Pipeline cancelled by user.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.CANCELLED,
                )
            ],
            **_telemetry(),
        }, "cancelled")

    attached = [cb for cb in scope.callbacks() if cb is not usage] if scope is not None else []
    # The run's budget stops it through this run's token (wave_a.md, section 5).
    for piece in attached:
        if isinstance(piece, Budget):
            piece.bind(token)
    hooks = hooks_of(ctx)

    def _invoke():
        # The run's scopes are pinned in the worker's context: each
        # datasource's Scope is built once, so every step decides the same way.
        # The run's RunScope is entered beside them, so LangGraph's own worker
        # threads inherit its trace_id and events reach it.
        with ctx.policy_engine.run(), (scope.entered() if scope is not None else contextlib.nullcontext()):
            state = initial_state.model_dump()
            # run.before: once, before the first step. It may rewrite the
            # question or end the run before anything else runs.
            verdict = hooks.call("run.before", trace_id=initial_state.trace_id, question=user_query,
                                 user=initial_state.user_context, datasource_id=datasource_id, execute=execute)
            if not verdict.allowed:
                return {**state, "errors": [verdict.error("orchestrator")]}
            state["user_query"] = verdict.ctx.question
            state["approvals"] = approvals
            pin = pinned or (pinned_from_approvals(approvals, question=verdict.ctx.question,
                                                   user=initial_state.user_context, hooks=hooks)
                             if approvals else None)
            state["pinned"] = pin
            # An approval's pin is known only now, after run.before: its run takes the pinned graph.
            run_graph = _graph(pin) if pinned is None and pin is not None and pin.enters_pinned else graph
            return run_graph.invoke(
                state,
                config={
                    "configurable": {"cancellation_token": token, "stream_answer": stream_answer},
                    "callbacks": [_StopWhenCancelled(token), *(callbacks or []), *attached, timing, usage,
                                  recorder],
                },
            )

    # Deliberately not a ``with`` block: ``ThreadPoolExecutor.__exit__`` calls
    # ``shutdown(wait=True)``, which joins the worker, so a timed-out
    # ``future.result`` would still have to wait for the whole graph to finish
    # before the timeout could surface. One worker per run; the run owns it.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="nl2sql-run")
    future = None
    try:
        # The worker starts from the caller's context (a request's keys, a
        # stream's thread), as LangGraph's own workers start from the worker's.
        future = pool.submit(contextvars.copy_context().run, _invoke)
        # Released when the graph is done, which a timed-out run is not yet.
        future.add_done_callback(lambda _: run_llms.release())
        try:
            result = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            # A budget stop the worker was still unwinding from is the run's ending, not the timeout.
            if isinstance(token.reason, RunStop):
                return _stopped(token.reason)
            # The worker keeps running the graph until its nodes observe the
            # cancelled token. That is a background wind-down, not a hang: the
            # caller is answered now.
            token.cancel()
            error_msg = f"Pipeline execution timed out after {timeout_sec} seconds."
            return _done({
                "errors": [
                    PipelineError(
                        node="orchestrator",
                        message=error_msg,
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.PIPELINE_TIMEOUT,
                    )
                ],
                # Written where the answer synthesizer would have written it:
                # `result_from_state` reads `final_answer` off
                # `answer_synthesizer_response` and nowhere else, so a
                # top-level key never reached the caller, who saw only the
                # error code. The shape is `AggregatedResponse`'s.
                **({"answer_synthesizer_response": {"final_answer": {
                    "summary": TIMEOUT_ANSWER,
                    "format_type": "text",
                    "content": TIMEOUT_ANSWER,
                    "warnings": [],
                }}} if answer else {}),
                **_telemetry(),
            }, "timeout", error_msg)

        # Nodes observe the token and unwind, so a cancelled run usually
        # returns normally; one stopped between steps raised RunCancelled.
        if token.is_cancelled():
            return _cancelled()
        result = dict(result)
        result.update(_telemetry())
        return _done(result, "completed")
    except PipelineExecutionError as e:
        # Raised where a PipelineError cannot be returned as a value (conditional-edge
        # routers). The payload is already structured, so surface it unchanged: the
        # blanket catch below would relabel it UNKNOWN_ERROR and flip is_retryable.
        return _done({"errors": [e.error]}, "crashed", e.error.message)
    except Exception as e:
        if token.is_cancelled():
            return _cancelled()
        # Fallback for other runtime crashes. A provider failure that escaped
        # its node still gets its own code rather than UNKNOWN_ERROR.
        return _done({
            "errors": [
                provider_error("orchestrator", e) or PipelineError(
                    node="orchestrator",
                    message=f"Pipeline crashed: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.UNKNOWN_ERROR,
                    stack_trace=traceback.format_exc(),
                )
            ]
        }, "crashed", f"{type(e).__name__}: {e}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        run_llms.unpin()
        if future is None:
            run_llms.release()
        run_done.set()
        restore_signals()
