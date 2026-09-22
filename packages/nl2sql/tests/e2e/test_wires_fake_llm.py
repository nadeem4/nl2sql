"""Whole questions through ``nl2sql run`` on each wire type, against the fake endpoints.

``FakeLLMServer`` answers ``/v1/chat/completions`` the way OpenAI does and
``/v1/messages`` the way Anthropic does (a ``tool_use`` block per
structured-output call), so the real clients, the nodes' parsing and the usage
telemetry all run with no key: once all on Claude, once with the planner on
Claude and every other node on OpenAI.
"""
from __future__ import annotations

import json

import pytest
import yaml

from nl2sql.testing.fake_llm import FakeLLMServer, Rule

from .conftest import _base_env, run_cli
from .recordings_chinook import RULES_COUNT_CUSTOMERS

pytest.importorskip("langchain_anthropic")

FAKE_KEY = "-".join(["fake", "anthropic", "key", "for", "e2e"])
OPENAI_FAKE_KEY = "-".join(["fake", "openai", "key", "for", "e2e"])
QUESTION = "How many customers are there?"

# What Anthropic reports for a call whose system prompt was read from the cache.
CACHED_USAGE = {"input_tokens": 60, "cache_read_input_tokens": 3500, "cache_creation_input_tokens": 0,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
                "output_tokens": 200}


@pytest.mark.e2e
def test_a_question_runs_end_to_end_on_claude(demo_project, tmp_path):
    rules = [Rule(r.name, r.payload, r.when, CACHED_USAGE) for r in RULES_COUNT_CUSTOMERS]
    server = FakeLLMServer(rules).start()
    try:
        cfg = {"version": 1, "agents": {},
               "default": {"provider": "anthropic", "model": "claude-opus-5", "temperature": None,
                           "base_url": server.anthropic_base_url, "api_key": "${env:ANTHROPIC_API_KEY}"}}
        (demo_project / "configs" / "llm.claude.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        env = _base_env()
        env.update({"ANTHROPIC_API_KEY": FAKE_KEY, "TRACE_MODE": "always", "TRACE_DIR": str(tmp_path)})
        r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.claude.yaml", QUESTION)
    finally:
        server.stop()

    assert r.returncode == 0, r.stdout + r.stderr
    assert [c["name"] for c in server.calls] == [
        "AnswerabilityResponse", "DecomposerResponse", "PlanModel", "AggregatedResponse"]
    assert all(c["mode"] == "anthropic" and c["api_key"] == FAKE_KEY for c in server.calls)

    sent = {c["name"]: c["body"] for c in server.calls}
    assert sent["AnswerabilityResponse"]["tool_choice"] == {"type": "tool", "name": "AnswerabilityResponse"}
    # The resolver's prefix (~260 tokens of system prompt plus a small tool
    # schema) is marked too, but is below every Claude model's cache minimum,
    # so Anthropic silently does not cache it.
    for name in ("AnswerabilityResponse", "DecomposerResponse", "PlanModel"):
        # The stable system prompt ends in the cache breakpoint; the question follows it.
        assert sent[name]["system"][-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in json.dumps(sent[name]["messages"])
        assert "temperature" not in sent[name]
    assert "system" not in sent["AggregatedResponse"]

    [path] = list(tmp_path.glob("*.json"))
    usage = json.loads(path.read_text(encoding="utf-8"))["result"]["usage"]["total"]
    assert usage["calls"] == 4
    assert usage["input_tokens"] == 4 * 3560
    assert usage["cached_input_tokens"] == 4 * 3500
    assert usage["cache_write_input_tokens"] == 0
    assert usage["output_tokens"] == 4 * 200


# OpenAI's usage for the same call: prompt_tokens already includes the cached part.
OPENAI_CACHED_USAGE = {"prompt_tokens": 3560, "completion_tokens": 200, "total_tokens": 3760,
                       "prompt_tokens_details": {"cached_tokens": 3500}}


@pytest.mark.e2e
def test_a_mixed_config_sends_each_node_to_its_own_wire(demo_project, tmp_path):
    """The planner on Claude, every other node on OpenAI, in one question."""
    rules = [Rule(r.name, r.payload, r.when, CACHED_USAGE if r.name == "PlanModel" else OPENAI_CACHED_USAGE)
             for r in RULES_COUNT_CUSTOMERS]
    server = FakeLLMServer(rules).start()
    try:
        cfg = {"version": 1,
               "default": {"provider": "openai", "model": "gpt-5.4", "temperature": 0.0,
                           "base_url": server.base_url, "api_key": "${env:OPENAI_API_KEY}"},
               "agents": {"astplanner": {"provider": "anthropic", "model": "claude-opus-5", "temperature": None,
                                         "base_url": server.anthropic_base_url,
                                         "api_key": "${env:ANTHROPIC_API_KEY}"}}}
        (demo_project / "configs" / "llm.mixed.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        env = _base_env()
        env.update({"OPENAI_API_KEY": OPENAI_FAKE_KEY, "ANTHROPIC_API_KEY": FAKE_KEY,
                    "TRACE_MODE": "always", "TRACE_DIR": str(tmp_path)})
        r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.mixed.yaml", QUESTION)
    finally:
        server.stop()

    assert r.returncode == 0, r.stdout + r.stderr
    # Which endpoint each node hit: /v1/messages is "anthropic", /v1/chat/completions "tools".
    assert [(c["name"], c["mode"]) for c in server.calls] == [
        ("AnswerabilityResponse", "tools"), ("DecomposerResponse", "tools"),
        ("PlanModel", "anthropic"), ("AggregatedResponse", "tools")]
    sent = {c["name"]: c for c in server.calls}
    assert sent["PlanModel"]["api_key"] == FAKE_KEY
    assert sent["PlanModel"]["body"]["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert sent["DecomposerResponse"]["authorization"] == f"Bearer {OPENAI_FAKE_KEY}"
    assert "cache_control" not in json.dumps(sent["DecomposerResponse"]["body"])

    doc = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert doc["llm"]["by_node"]["ast_planner"] == "claude-opus-5"
    assert doc["llm"]["by_node"]["decomposer"] == "gpt-5.4"
    # Each wire's usage lands in the same fields.
    nodes = doc["result"]["usage"]["nodes"]
    for node in ("datasource_resolver", "decomposer", "ast_planner", "answer_synthesizer"):
        assert (nodes[node]["input_tokens"], nodes[node]["cached_input_tokens"]) == (3560, 3500), node
