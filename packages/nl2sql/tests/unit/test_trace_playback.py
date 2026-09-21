"""``TracePlayback`` answers each model call from the recording, keyed by node, sub-query and attempt."""
from __future__ import annotations

import json

from nl2sql.tracing.replay import TracePlayback, diff_results, prompt_sha256


def _call(node, sq, attempt, messages, response, usage=None):
    return {
        "key": {"node": node, "sub_query_id": sq, "attempt": attempt, "call_index": 1},
        "messages": messages,
        "prompt_sha256": prompt_sha256(messages),
        "response": response,
        "usage": usage or {"input_tokens": 11000, "cached_input_tokens": 7680, "output_tokens": 300,
                           "reasoning_tokens": 128, "total_tokens": 11300},
    }


PLAN_MSGS = [{"role": "user", "content": "[USER_QUERY]\nHow many customers"}]
RETRY_MSGS = [{"role": "user", "content": "[USER_QUERY]\nHow many customers\nfeedback: bad column"}]


def _trace():
    return {"nodes": [
        {"node": "ast_planner", "sub_query_id": "sq1", "attempt": 1, "llm_calls": [
            _call("ast_planner", "sq1", 1, PLAN_MSGS, {"content": '{"tables": ["bad"]}', "tool_calls": []})]},
        {"node": "ast_planner", "sub_query_id": "sq1", "attempt": 2, "llm_calls": [
            _call("ast_planner", "sq1", 2, RETRY_MSGS, {"content": '{"tables": ["good"]}', "tool_calls": []})]},
        {"node": "decomposer", "sub_query_id": None, "attempt": 1, "llm_calls": [
            _call("decomposer", None, 1, PLAN_MSGS,
                  {"content": "", "tool_calls": [{"id": "c1", "name": "DecomposerResponse", "args": {"x": 1}}]})]},
    ]}


def _body(messages, mode="json_schema", name="PlanModel"):
    body = {"model": "gpt-4o", "messages": messages}
    if mode == "json_schema":
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": name}}
    elif mode == "tools":
        body["tools"] = [{"type": "function", "function": {"name": name}}]
    return body


def test_the_same_question_gets_a_different_answer_per_attempt():
    playback = TracePlayback(_trace())
    first = playback.respond({"node": "ast_planner", "sub_query_id": "sq1", "attempt": 1, "call_index": 1},
                             _body(PLAN_MSGS))
    second = playback.respond({"node": "ast_planner", "sub_query_id": "sq1", "attempt": 2, "call_index": 1},
                              _body(RETRY_MSGS))
    assert first["choices"][0]["message"]["content"] == '{"tables": ["bad"]}'
    assert second["choices"][0]["message"]["content"] == '{"tables": ["good"]}'
    # The recorded usage comes back in OpenAI's shape.
    usage = second["usage"]
    assert usage["prompt_tokens"] == 11000 and usage["prompt_tokens_details"]["cached_tokens"] == 7680
    assert usage["completion_tokens_details"]["reasoning_tokens"] == 128
    assert playback.divergence is None and playback.served == 2


def test_a_tool_call_is_answered_as_a_tool_call():
    playback = TracePlayback(_trace())
    out = playback.respond({"node": "decomposer", "sub_query_id": None, "attempt": 1, "call_index": 1},
                           _body(PLAN_MSGS, mode="tools", name="DecomposerResponse"))
    [tool_call] = out["choices"][0]["message"]["tool_calls"]
    assert tool_call["function"]["name"] == "DecomposerResponse"
    assert json.loads(tool_call["function"]["arguments"]) == {"x": 1}


def test_a_call_the_recording_does_not_have_is_a_divergence():
    playback = TracePlayback(_trace())
    out = playback.respond({"node": "ast_planner", "sub_query_id": "sq1", "attempt": 3, "call_index": 1},
                           _body(RETRY_MSGS))
    assert out is None
    d = playback.divergence
    assert d.node == "ast_planner" and d.sub_query_id == "sq1" and d.attempt == 3
    assert d.reason == "missing"


def test_a_changed_prompt_is_a_divergence_with_the_difference():
    playback = TracePlayback(_trace())
    changed = [{"role": "user", "content": "[USER_QUERY]\nHow many customers, please"}]
    out = playback.respond({"node": "ast_planner", "sub_query_id": "sq1", "attempt": 1, "call_index": 1},
                           _body(changed))
    assert out is None
    d = playback.divergence
    assert d.reason == "prompt_changed" and d.node == "ast_planner"
    assert "please" in d.detail


def test_only_the_first_divergence_is_kept():
    playback = TracePlayback(_trace())
    playback.respond({"node": "x", "sub_query_id": None, "attempt": 1, "call_index": 1}, _body(PLAN_MSGS))
    playback.respond({"node": "y", "sub_query_id": None, "attempt": 1, "call_index": 1}, _body(PLAN_MSGS))
    assert playback.divergence.node == "x"


def test_unused_recorded_calls_are_listed():
    playback = TracePlayback(_trace())
    assert len(playback.unused_calls()) == 3


def test_result_diff_ignores_timings_and_ids_but_not_sql_or_rows():
    recorded = {"status": "success", "trace_id": "a", "timings": {"x": 1.0},
                "sub_queries": [{"id": "sq1", "sql": "SELECT 1", "rows": {"rows": [[59]]}, "retry_count": 1}]}
    same = {**recorded, "trace_id": "b", "timings": {"x": 2.0}}
    assert diff_results(recorded, same) == []
    other = {**recorded, "sub_queries": [{"id": "sq1", "sql": "SELECT 2", "rows": {"rows": [[60]]},
                                          "retry_count": 1}]}
    changes = diff_results(recorded, other)
    assert any("sql" in c for c in changes) and any("rows" in c for c in changes)
