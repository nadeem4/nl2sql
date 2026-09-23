"""``run_with_graph`` reports failures in state instead of raising, without losing
their identity. Cancellation is scoped to a single run, not to the whole process.
"""
from __future__ import annotations

import threading
import time

from nl2sql.auth import UserContext
from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.exceptions import PipelineExecutionError
from nl2sql.api import query_api
from nl2sql.pipeline import runtime


def test_tokens_are_independent():
    a, b = CancellationToken(), CancellationToken()
    a.cancel()
    assert a.is_cancelled()
    assert not b.is_cancelled()


def test_wait_returns_true_only_once_cancelled():
    token = CancellationToken()
    assert token.wait(timeout=0.01) is False
    token.cancel()
    assert token.wait(timeout=0.01) is True


_USER = UserContext(roles=["user"])


class _FakeGraph:
    """Stands in for the compiled LangGraph pipeline (no LLM, DB or network)."""

    def __init__(self, on_invoke):
        self._on_invoke = on_invoke

    def invoke(self, state, config=None):
        return self._on_invoke(state, config)


def _use_fake_graph(monkeypatch, on_invoke):
    monkeypatch.setattr(
        runtime,
        "build_graph",
        lambda ctx, execute=True: _FakeGraph(on_invoke),
    )


def test_each_run_gets_its_own_token(monkeypatch):
    seen = []

    def on_invoke(state, config):
        seen.append(config["configurable"]["cancellation_token"])
        return {"final_answer": "ok"}

    _use_fake_graph(monkeypatch, on_invoke)

    runtime.run_with_graph(None, "first query", user_context=_USER)
    runtime.run_with_graph(None, "second query", user_context=_USER)

    assert len(seen) == 2
    assert seen[0] is not seen[1]
    seen[0].cancel()
    assert not seen[1].is_cancelled()


def test_cancelled_run_returns_cancelled_error(monkeypatch):
    def on_invoke(state, config):
        # Simulates Ctrl+X / SIGINT firing mid-run: the token trips and the
        # nodes unwind, so the graph itself returns normally with partial state.
        config["configurable"]["cancellation_token"].cancel()
        return {"final_answer": "partial answer"}

    _use_fake_graph(monkeypatch, on_invoke)

    result = runtime.run_with_graph(None, "cancelled query", user_context=_USER)

    assert "final_answer" not in result
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error.node == "orchestrator"
    assert error.error_code == ErrorCode.CANCELLED
    assert error.severity == ErrorSeverity.ERROR


def test_run_exceeding_timeout_returns_pipeline_timeout_error(monkeypatch):
    monkeypatch.setattr(runtime.settings, "global_timeout_sec", 0.05)

    def on_invoke(state, config):
        time.sleep(0.5)
        return {"final_answer": "too late"}

    _use_fake_graph(monkeypatch, on_invoke)

    result = runtime.run_with_graph(None, "slow query", user_context=_USER)

    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error.node == "orchestrator"
    assert error.error_code == ErrorCode.PIPELINE_TIMEOUT
    assert "timed out after 0.05 seconds" in error.message


def test_a_timed_out_run_tells_the_caller_so(monkeypatch):
    # The apology used to be written to a top-level "final_answer" key, and
    # `result_from_state` only ever reads `answer_synthesizer_response`, so
    # every caller through QueryAPI and the REST route saw `final_answer: null`
    # with nothing but a PIPELINE_TIMEOUT code.
    # Arrange
    monkeypatch.setattr(runtime.settings, "global_timeout_sec", 0.05)

    def on_invoke(state, config):
        time.sleep(0.5)
        return {"final_answer": "too late"}

    _use_fake_graph(monkeypatch, on_invoke)

    # Act
    result = query_api.result_from_state(runtime.run_with_graph(None, "slow query", user_context=_USER))

    # Assert
    assert result.status == "error"
    assert result.final_answer is not None, "the timeout apology never reached the caller"
    assert result.final_answer["summary"].startswith("I apologize")
    assert result.final_answer["format_type"] == "text"
    assert [e["error_code"] for e in result.errors] == [ErrorCode.PIPELINE_TIMEOUT.value]


def test_routing_error_keeps_its_own_error_code(monkeypatch):
    # A PipelineExecutionError already carries a fully-populated PipelineError. The
    # runtime must surface that payload as-is: relabelling it UNKNOWN_ERROR reports
    # the wrong code and flips is_retryable for every downstream handler.
    # Arrange
    routing_error = PipelineError(
        node="layer_router",
        message="No compatible subgraph found for datasource 'rest_ds'.",
        severity=ErrorSeverity.ERROR,
        error_code=ErrorCode.INVALID_STATE,
    )

    def on_invoke(state, config):
        raise PipelineExecutionError(routing_error)

    _use_fake_graph(monkeypatch, on_invoke)

    # Act
    result = runtime.run_with_graph(None, "unroutable query", user_context=_USER)

    # Assert
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error.error_code == ErrorCode.INVALID_STATE
    assert error.severity == ErrorSeverity.ERROR
    assert error.node == "layer_router"
    assert error.message == "No compatible subgraph found for datasource 'rest_ds'."
    assert error.is_retryable is False
    # Deliberate, well-understood failure: no traceback is added, matching the
    # cancellation and timeout branches.
    assert error.stack_trace is None


def test_unexpected_crash_still_reports_unknown_error_with_a_stack_trace(monkeypatch):
    # The blanket catch stays a safety net: anything the runtime cannot recognise
    # is still reported as UNKNOWN_ERROR with a traceback to debug from.
    # Arrange
    def on_invoke(state, config):
        raise RuntimeError("something nobody planned for")

    _use_fake_graph(monkeypatch, on_invoke)

    # Act
    result = runtime.run_with_graph(None, "crashing query", user_context=_USER)

    # Assert
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error.node == "orchestrator"
    assert error.error_code == ErrorCode.UNKNOWN_ERROR
    assert "something nobody planned for" in error.message
    assert "RuntimeError" in (error.stack_trace or "")


def test_timeout_fires_promptly_and_cancels_token(monkeypatch):
    # The pool must not be a context manager: its ``__exit__`` joins the worker,
    # so the timeout would only surface once the graph finished, however late.
    seen = {}

    def on_invoke(state, config):
        seen["token"] = config["configurable"]["cancellation_token"]
        time.sleep(2.0)
        return {"final_answer": "late"}

    _use_fake_graph(monkeypatch, on_invoke)
    monkeypatch.setattr(runtime.settings, "global_timeout_sec", 0.2)

    start = time.perf_counter()
    result = runtime.run_with_graph(None, "slow query", user_context=_USER)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"timeout surfaced after {elapsed:.2f}s"
    assert result["errors"][0].error_code == ErrorCode.PIPELINE_TIMEOUT
    assert seen["token"].is_cancelled()


def test_run_from_worker_thread_does_not_raise(monkeypatch):
    # FastAPI runs the sync ``/query`` handler in Starlette's threadpool, and
    # ``signal.signal`` raises ValueError anywhere but the main thread.
    _use_fake_graph(monkeypatch, lambda state, config: {"final_answer": "ok"})
    box = {}

    def worker():
        try:
            box["result"] = runtime.run_with_graph(None, "q", user_context=_USER)
        except BaseException as exc:  # noqa: BLE001
            box["exc"] = exc

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert "exc" not in box, box.get("exc")
    assert box["result"]["final_answer"] == "ok"
