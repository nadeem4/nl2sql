"""``run_with_graph`` writes the run's trace according to ``TRACE_MODE``."""
from __future__ import annotations

import json
import uuid

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from nl2sql.api.query_api import result_from_state
from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.settings import settings
from nl2sql.pipeline import runtime
from nl2sql.tracing.recorder import TraceRecorder

FAKE_KEY = "sk-test-not-a-real-key-1234"


class _FakeGraph:
    def __init__(self, on_invoke):
        self._on_invoke = on_invoke

    def invoke(self, state, config=None):
        return self._on_invoke(state, config)


def _planner_run(errors):
    """Fires the callbacks a one-node, one-LLM-call run would, then returns ``errors``."""

    def _invoke(state, config):
        [rec] = [c for c in config["callbacks"] if isinstance(c, TraceRecorder)]
        cbs = config["callbacks"]
        node_run, llm_run = uuid.uuid4(), uuid.uuid4()
        meta = {"langgraph_node": "ast_planner"}
        for cb in cbs:
            if hasattr(cb, "on_chain_start"):
                cb.on_chain_start({}, {"user_query": state["user_query"]}, run_id=node_run,
                                  parent_run_id=None, metadata=meta, name="ast_planner")
        for cb in cbs:
            if type(cb).on_chat_model_start is BaseCallbackHandler.on_chat_model_start:
                continue  # the base class raises NotImplementedError, as LangChain expects
            cb.on_chat_model_start({}, [[HumanMessage(content=f"plan it; key={FAKE_KEY}")]], run_id=llm_run,
                                   parent_run_id=node_run, metadata=meta,
                                   invocation_params={"model": "gpt-4o", "api_key": FAKE_KEY})
        msg = AIMessage(content="{}", usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7})
        for cb in cbs:
            cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id=llm_run)
        for cb in cbs:
            if hasattr(cb, "on_chain_end"):
                cb.on_chain_end({"errors": errors}, run_id=node_run)
        assert rec is not None
        return {"trace_id": state["trace_id"], "errors": errors}

    return _invoke


_ERROR = PipelineError(node="ast_planner", message="Planner failed.", severity=ErrorSeverity.ERROR,
                       error_code=ErrorCode.PLANNING_FAILURE)


@pytest.fixture
def trace_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "trace_dir", str(tmp_path))
    monkeypatch.setattr(settings, "openai_api_key", FAKE_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    return tmp_path


def _run(monkeypatch, mode, errors):
    monkeypatch.setattr(settings, "trace_mode", mode)
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _FakeGraph(_planner_run(errors)))
    return runtime.run_with_graph(None, "how many customers?", user_context=UserContext(roles=["admin"]))


def test_default_mode_is_on_failure(monkeypatch):
    for name in ("TRACE_MODE", "TRACE_DIR", "TRACE_SAMPLE_ROWS"):
        monkeypatch.delenv(name, raising=False)
    from nl2sql.common.settings import Settings

    assert Settings().trace_mode == "on_failure"
    assert Settings().trace_dir == "traces"
    assert Settings().trace_sample_rows == 50


def test_an_invalid_mode_is_rejected(monkeypatch):
    from nl2sql.common.settings import Settings

    monkeypatch.setenv("TRACE_MODE", "sometimes")
    with pytest.raises(ValueError):
        Settings()


def test_on_failure_writes_a_failed_run_and_says_where(trace_dir, monkeypatch):
    state = _run(monkeypatch, "on_failure", [_ERROR])
    [path] = list(trace_dir.glob("*.json"))
    assert state["trace_path"] == str(path)
    assert path.name.endswith(f"_{state['trace_id']}.json")
    assert result_from_state(state).trace_path == str(path)

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["trace_format_version"] == 1
    assert doc["trace_id"] == state["trace_id"]
    assert doc["request"]["question"] == "how many customers?"
    assert doc["request"]["roles"] == ["admin"]
    assert doc["failed"] is True
    assert doc["engine"]["version"]
    [node] = doc["nodes"]
    assert node["node"] == "ast_planner" and node["status"] == "error"
    [call] = node["llm_calls"]
    assert call["usage"]["input_tokens"] == 5
    assert doc["result"]["errors"][0]["error_code"] == "PLANNING_FAILURE"


def test_on_failure_skips_a_clean_run(trace_dir, monkeypatch):
    state = _run(monkeypatch, "on_failure", [])
    assert list(trace_dir.glob("*.json")) == []
    assert state.get("trace_path") is None


def test_always_writes_a_clean_run(trace_dir, monkeypatch):
    _run(monkeypatch, "always", [])
    assert len(list(trace_dir.glob("*.json"))) == 1


def test_off_writes_nothing(trace_dir, monkeypatch):
    _run(monkeypatch, "off", [_ERROR])
    assert list(trace_dir.glob("*.json")) == []


def test_no_secret_reaches_the_file(trace_dir, monkeypatch):
    _run(monkeypatch, "always", [])
    [path] = list(trace_dir.glob("*.json"))
    text = path.read_text(encoding="utf-8")
    assert FAKE_KEY not in text
    assert "[REDACTED]" in text


def test_the_settings_snapshot_holds_no_secret_fields(trace_dir, monkeypatch):
    _run(monkeypatch, "always", [])
    [path] = list(trace_dir.glob("*.json"))
    snapshot = json.loads(path.read_text(encoding="utf-8"))["settings"]
    assert "openai_api_key" not in snapshot
    assert snapshot["trace_mode"] == "always"
