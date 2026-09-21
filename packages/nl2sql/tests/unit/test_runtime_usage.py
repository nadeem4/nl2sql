"""``run_with_graph`` attaches the usage callback on every run, whatever the caller."""
from __future__ import annotations

import uuid

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from nl2sql.auth import UserContext
from nl2sql.pipeline import runtime
from nl2sql.services.callbacks.token_handler import TokenUsageCallback


class _FakeGraph:
    def __init__(self, on_invoke):
        self._on_invoke = on_invoke

    def invoke(self, state, config=None):
        return self._on_invoke(state, config)


def _one_planner_call(state, config):
    [cb] = [c for c in config["callbacks"] if isinstance(c, TokenUsageCallback)]
    run = uuid.uuid4()
    cb.on_chat_model_start({}, [[]], run_id=run, metadata={"langgraph_node": "ast_planner"})
    msg = AIMessage(content="", usage_metadata={
        "input_tokens": 11000, "output_tokens": 300, "total_tokens": 11300,
        "input_token_details": {"cache_read": 7680}, "output_token_details": {"reasoning": 128}})
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id=run)
    return {"final_answer": "ok"}


def test_every_run_reports_usage_without_the_caller_passing_a_callback(monkeypatch):
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: _FakeGraph(_one_planner_call))

    state = runtime.run_with_graph(None, "q", user_context=UserContext(roles=["user"]))

    usage = state["usage"]
    assert usage["total"]["calls"] == 1
    assert usage["nodes"]["ast_planner"]["cached_input_tokens"] == 7680
    assert usage["nodes"]["ast_planner"]["reasoning_tokens"] == 128

