from __future__ import annotations

import concurrent.futures
import signal
import sys
import threading
import traceback
from typing import Callable, Dict, List, Optional

from nl2sql.auth import UserContext
from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.graph import build_graph
from nl2sql.pipeline.state import GraphState


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
) -> Dict:
    """Convenience function to run the full pipeline."""
    token = CancellationToken()
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

    def _invoke():
        return graph.invoke(
            initial_state.model_dump(),
            config={
                "configurable": {"cancellation_token": token},
                "callbacks": callbacks,
            },
        )

    try:
        # Use configured thread pool size for pipeline execution
        with concurrent.futures.ThreadPoolExecutor(max_workers=settings.sandbox_exec_workers) as executor:
            future = executor.submit(_invoke)
            result = future.result(timeout=timeout_sec)

        # Nodes observe the token and unwind, so a cancelled run returns normally.
        if token.is_cancelled():
            return {
                "errors": [
                    PipelineError(
                        node="orchestrator",
                        message="Pipeline cancelled by user.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.CANCELLED,
                    )
                ]
            }
        return result
    except concurrent.futures.TimeoutError:
        error_msg = f"Pipeline execution timed out after {timeout_sec} seconds."
        return {
            "errors": [
                PipelineError(
                    node="orchestrator",
                    message=error_msg,
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.PIPELINE_TIMEOUT,
                )
            ],
            "final_answer": "I apologize, but the request timed out. Please try again with a simpler query.",
        }
    except Exception as e:
        # Fallback for other runtime crashes
        return {
            "errors": [
                PipelineError(
                    node="orchestrator",
                    message=f"Pipeline crashed: {str(e)}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.UNKNOWN_ERROR,
                    stack_trace=traceback.format_exc(),
                )
            ]
        }
    finally:
        run_done.set()
        restore_signals()
