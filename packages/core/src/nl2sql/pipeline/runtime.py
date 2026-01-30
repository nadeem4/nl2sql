from __future__ import annotations

import concurrent.futures
import signal
import sys
import threading
import time
import traceback
from typing import Callable, Dict, List, Optional

from nl2sql.auth import UserContext
from nl2sql.common.cancellation import cancel, is_cancelled, reset
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.settings import settings
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.graph import build_graph
from nl2sql.pipeline.state import GraphState

_keyboard_listener_started = False


def _start_keyboard_cancel_listener() -> None:
    global _keyboard_listener_started
    if _keyboard_listener_started:
        return
    _keyboard_listener_started = True

    if sys.platform != "win32":
        return

    if not sys.stdin or not sys.stdin.isatty():
        return

    try:
        import msvcrt
    except Exception:
        return

    def _listen():
        while True:
            if msvcrt.kbhit():
                char = msvcrt.getch()
                if char == b"\x18":  # Ctrl+X
                    cancel()
                    return

    threading.Thread(target=_listen, daemon=True).start()


def _install_signal_handlers() -> Callable[[], None]:
    previous = {}

    def _handler(signum, frame):
        cancel()

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
    reset()
    restore_signals = _install_signal_handlers()
    _start_keyboard_cancel_listener()

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
            config={"callbacks": callbacks},
        )

    try:
        # Use configured thread pool size for pipeline execution
        with concurrent.futures.ThreadPoolExecutor(max_workers=settings.sandbox_exec_workers) as executor:
            future = executor.submit(_invoke)
            start_time = time.monotonic()
            while True:
                if is_cancelled():
                    future.cancel()
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
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout_sec:
                    raise concurrent.futures.TimeoutError()

                try:
                    return future.result(timeout=0.25)
                except concurrent.futures.TimeoutError:
                    continue
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
        restore_signals()
