from __future__ import annotations

import concurrent.futures
import datetime as _dt
import signal
import sys
import threading
import time
import traceback
from typing import Callable, Dict, List, Optional

from nl2sql.auth import UserContext
from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.exceptions import PipelineExecutionError
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.graph import build_graph
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.timing import NodeTimingCallback
from nl2sql.services.callbacks.token_handler import TokenUsageCallback
from nl2sql.tracing.recorder import TraceRecorder
from nl2sql.tracing.trace import write_run_trace


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


def run_with_graph(
    ctx: NL2SQLContext,
    user_query: str,
    datasource_id: Optional[str] = None,
    execute: bool = True,
    callbacks: Optional[List] = None,
    user_context: UserContext = None,
    cancellation_token: Optional[CancellationToken] = None,
    trace_mode: Optional[str] = None,
) -> Dict:
    """Convenience function to run the full pipeline.

    Every run is recorded by a ``TraceRecorder``; ``TRACE_MODE`` (or
    ``trace_mode``, which wins) decides whether the trace is written. When it
    is, the returned state carries ``trace_path``. ``cancellation_token`` lets
    a caller stop the run (trace replay does, on divergence).
    """
    token = cancellation_token or CancellationToken()
    run_done = threading.Event()
    restore_signals = _install_signal_handlers(token)
    _start_keyboard_cancel_listener(token, run_done)

    graph = build_graph(
        ctx,
        execute=execute,
    )

    initial_state = GraphState(
        user_query=user_query,
        user_context=user_context,
        datasource_id=datasource_id,
    )

    timeout_sec = settings.global_timeout_sec
    timing = NodeTimingCallback()
    usage = TokenUsageCallback(prices=settings.llm_prices)
    recorder = TraceRecorder()
    started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    t0 = time.perf_counter()

    def _telemetry() -> Dict:
        """Timings and token usage so far; also reported for a timed-out or cancelled run."""
        return {"timings": dict(timing.timings), "usage": usage.usage().model_dump(mode="json")}

    def _done(out: Dict, outcome: str, error: Optional[str] = None) -> Dict:
        """Stamps the trace id and, if the trace was written, where it went."""
        out.setdefault("trace_id", initial_state.trace_id)
        path = write_run_trace(
            trace_mode or settings.trace_mode,
            trace_id=initial_state.trace_id,
            request={
                "question": user_query,
                "roles": list(getattr(initial_state.user_context, "roles", []) or []),
                "tenant_id": getattr(initial_state.user_context, "tenant_id", None),
                "datasource_id": datasource_id,
                "execute": execute,
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
        )
        if path:
            out["trace_path"] = path
        return out

    def _invoke():
        return graph.invoke(
            initial_state.model_dump(),
            config={
                "configurable": {"cancellation_token": token},
                "callbacks": [*(callbacks or []), timing, usage, recorder],
            },
        )

    # Deliberately not a ``with`` block: ``ThreadPoolExecutor.__exit__`` calls
    # ``shutdown(wait=True)``, which joins the worker, so a timed-out
    # ``future.result`` would still have to wait for the whole graph to finish
    # before the timeout could surface. One worker per run; the run owns it.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="nl2sql-run")
    try:
        future = pool.submit(_invoke)
        try:
            result = future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
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
                "final_answer": "I apologize, but the request timed out. Please try again with a simpler query.",
                **_telemetry(),
            }, "timeout", error_msg)

        # Nodes observe the token and unwind, so a cancelled run returns normally.
        if token.is_cancelled():
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
        result = dict(result)
        result.update(_telemetry())
        return _done(result, "completed")
    except PipelineExecutionError as e:
        # Raised where a PipelineError cannot be returned as a value (conditional-edge
        # routers). The payload is already structured, so surface it unchanged: the
        # blanket catch below would relabel it UNKNOWN_ERROR and flip is_retryable.
        return _done({"errors": [e.error]}, "crashed", e.error.message)
    except Exception as e:
        # Fallback for other runtime crashes
        return _done({
            "errors": [
                PipelineError(
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
        run_done.set()
        restore_signals()
