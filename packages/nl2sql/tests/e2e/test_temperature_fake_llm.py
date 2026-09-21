"""Per-node models and optional temperature through a real CLI run.

``FakeLLMServer(reject_temperature=True)`` answers any request carrying a
temperature with the HTTP 400 gpt-5.5 returned on 2026-09-20, so the error a
user sees is checked end to end without a key or a real model.
"""
from __future__ import annotations

import json
import re

import pytest
import yaml

from nl2sql.testing.fake_llm import FakeLLMServer

from .conftest import _base_env, run_cli
from .recordings_chinook import RULES_COUNT_CUSTOMERS

FAKE_KEY = "-".join(["fake", "key", "for", "e2e"])
QUESTION = "How many customers are there?"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _write_config(demo_project, name, server, default, agents=None):
    common = {"provider": "openai", "base_url": server.base_url, "api_key": "${env:OPENAI_API_KEY}"}
    cfg = {"version": 1, "default": {**common, **default},
           "agents": {key: {**common, **agent} for key, agent in (agents or {}).items()}}
    (demo_project / "configs" / name).write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _env(trace_dir=None):
    env = _base_env()
    env["OPENAI_API_KEY"] = FAKE_KEY
    if trace_dir is not None:
        env.update({"TRACE_MODE": "always", "TRACE_DIR": str(trace_dir)})
    return env


@pytest.mark.e2e
def test_a_rejected_temperature_tells_the_user_what_to_set(demo_project):
    server = FakeLLMServer(RULES_COUNT_CUSTOMERS, reject_temperature=True).start()
    try:
        _write_config(demo_project, "llm.temp-rejected.yaml", server,
                      {"model": "gpt-5.5", "temperature": 0.0})
        r = run_cli(demo_project, _env(), "run", "--llm-config", "configs/llm.temp-rejected.yaml", QUESTION)
    finally:
        server.stop()

    out = ANSI.sub("", r.stdout + r.stderr)
    flat = re.sub(r"\s+", " ", out)  # Rich wraps long messages
    assert "Model 'gpt-5.5'" in flat, out
    assert "temperature: null" in flat, out
    assert "Traceback" not in out
    # The decomposer is the first LLM call; it failed, so nothing else was asked.
    assert [c["name"] for c in server.calls] == ["DecomposerResponse"]


@pytest.mark.e2e
def test_per_node_models_and_temperatures_reach_the_wire_and_the_trace(demo_project, tmp_path):
    server = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    try:
        _write_config(demo_project, "llm.per-node.yaml", server, {"model": "gpt-5.4"},
                      agents={"astplanner": {"model": "gpt-5.5", "temperature": None}})
        r = run_cli(demo_project, _env(tmp_path), "run", "--llm-config", "configs/llm.per-node.yaml", QUESTION)
    finally:
        server.stop()
    assert r.returncode == 0, r.stdout + r.stderr

    sent = {c["name"]: c["body"] for c in server.calls}
    assert sent["DecomposerResponse"]["model"] == "gpt-5.4"
    assert sent["DecomposerResponse"]["temperature"] == 0.0
    assert sent["PlanModel"]["model"] == "gpt-5.5"
    assert "temperature" not in sent["PlanModel"]
    assert sent["AggregatedResponse"]["model"] == "gpt-5.4"
    assert all(body["seed"] == 42 for body in sent.values())

    [path] = list(tmp_path.glob("*.json"))
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["llm"]["by_node"] == {"decomposer": "gpt-5.4", "ast_planner": "gpt-5.5",
                                     "answer_synthesizer": "gpt-5.4"}
    assert doc["llm"]["configured"]["astplanner"] == {"provider": "openai", "model": "gpt-5.5",
                                                      "temperature": None}
    params = {call["key"]["node"]: call["params"]
              for node in doc["nodes"] for call in node["llm_calls"]}
    assert "temperature" not in params["ast_planner"]
    assert params["decomposer"]["temperature"] == 0.0
    usage_models = {c["node"]: c["model"] for c in doc["result"]["usage"]["calls"]}
    assert usage_models == {"decomposer": "gpt-5.4", "ast_planner": "gpt-5.5", "answer_synthesizer": "gpt-5.4"}

    # Replay rebuilds each agent's client the same way and serves the recording.
    replay = run_cli(demo_project, _env(), "trace", "replay", str(path))
    replay_out = ANSI.sub("", replay.stdout)
    assert replay.returncode == 0, replay_out + replay.stderr
    assert "matches the recording" in replay_out
