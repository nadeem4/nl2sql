"""Run traces end to end: written by a real run, shown, and replayed without a model.

Every run here goes through ``FakeLLMServer``; the key is fake and is also the
string the trace must never contain.
"""
from __future__ import annotations

import copy
import json
import re
from types import SimpleNamespace

import pytest
import yaml

from nl2sql.testing.fake_llm import FakeLLMServer, Rule

from .conftest import _base_env, run_cli
from .recordings_chinook import (
    COUNT_CUSTOMERS_DECOMPOSER,
    COUNT_CUSTOMERS_PLAN,
    RULES_COUNT_CUSTOMERS,
    count_customers_answer,
)

FAKE_KEY = "sk-test-not-a-real-key-1234"
QUESTION = "How many customers are there?"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Every node a counted, executed question passes through.
NODES = {
    "datasource_resolver", "decomposer", "global_planner", "layer_router", "sql_agent",
    "schema_retriever", "ast_planner", "logical_validator", "generator", "executor",
    "aggregator", "answer_synthesizer",
}


def _serve(demo_project, rules, name):
    server = FakeLLMServer(rules).start()
    cfg = {"version": 1, "default": {"provider": "openai", "model": "gpt-4o", "temperature": 0.0,
                                     "base_url": server.base_url, "api_key": "${env:OPENAI_API_KEY}",
                                     "name": "default"}, "agents": {}}
    (demo_project / "configs" / name).write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return server


def _env(trace_dir, mode):
    env = _base_env()
    env.update({"OPENAI_API_KEY": FAKE_KEY, "TRACE_MODE": mode, "TRACE_DIR": str(trace_dir),
                "SQL_AGENT_RETRY_BASE_DELAY_SEC": "0", "SQL_AGENT_RETRY_JITTER_SEC": "0"})
    return env


def _llm_calls(doc):
    return [call for node in doc["nodes"] for call in node["llm_calls"]]


@pytest.mark.e2e
def test_a_run_writes_a_trace_with_every_node_its_llm_calls_and_no_secret(demo_project, tmp_path):
    server = _serve(demo_project, RULES_COUNT_CUSTOMERS, "llm.trace-ok.yaml")
    try:
        r = run_cli(demo_project, _env(tmp_path, "always"), "run", "--llm-config", "configs/llm.trace-ok.yaml",
                    QUESTION)
    finally:
        server.stop()
    assert r.returncode == 0, r.stdout + r.stderr

    [path] = list(tmp_path.glob("*.json"))
    assert path.name in ANSI.sub("", r.stdout).replace("\n", "")  # the CLI says where it went
    text = path.read_text(encoding="utf-8")
    assert FAKE_KEY not in text

    doc = json.loads(text)
    assert doc["trace_format_version"] == 1
    assert doc["request"] == {"question": QUESTION, "roles": ["admin"], "tenant_id": "default_tenant",
                              "datasource_id": None, "execute": True}
    assert NODES <= {n["node"] for n in doc["nodes"]}
    assert doc["llm"]["by_node"] == {"decomposer": "gpt-4o", "ast_planner": "gpt-4o",
                                     "answer_synthesizer": "gpt-4o"}

    calls = _llm_calls(doc)
    assert [c["key"]["node"] for c in calls] == ["decomposer", "ast_planner", "answer_synthesizer"]
    for call in calls:
        assert call["messages"] and call["messages"][0]["content"]
        assert call["usage"]["total_tokens"] == 2  # the fake reports 1 + 1 per call
        assert call["model"].startswith("gpt-4o")
    decomposer, planner, synthesizer = calls
    assert decomposer["response"]["tool_calls"][0]["name"] == "DecomposerResponse"
    assert json.loads(planner["response"]["content"])["tables"][0]["name"] == "Customer"
    assert planner["parsed"]["tables"][0]["name"] == "Customer"
    assert "How many customers" in planner["messages"][-1]["content"]  # the question is in the last (human) message
    assert synthesizer["parsed"]["summary"] == "There are 59 customers."

    [sub] = doc["result"]["sub_queries"]
    assert sub["rows"]["rows"] == [[59]]
    assert doc["failed"] is False


def _retry_rules():
    """The first plan counts a column that does not exist; the second is right."""
    bad_plan = copy.deepcopy(COUNT_CUSTOMERS_PLAN)
    bad_plan["select_items"][0]["expr"]["args"][0]["column_name"] = "CustomerIdd"
    plans = []

    def plan(_text):
        plans.append(1)
        return bad_plan if len(plans) == 1 else COUNT_CUSTOMERS_PLAN

    return [
        Rule("DecomposerResponse", COUNT_CUSTOMERS_DECOMPOSER),
        Rule("PlanModel", plan),
        Rule("AggregatedResponse", count_customers_answer),
        Rule("plain", "Count Customer.CustomerId; CustomerIdd does not exist."),
    ]


@pytest.fixture(scope="module")
def failing_run(demo_project, tmp_path_factory):
    traces = tmp_path_factory.mktemp("traces")
    server = _serve(demo_project, _retry_rules(), "llm.trace-retry.yaml")
    env = _env(traces, "on_failure")
    run = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.trace-retry.yaml", QUESTION)
    try:
        yield SimpleNamespace(root=demo_project, server=server, env=env, run=run, traces=traces)
    finally:
        server.stop()


@pytest.mark.e2e
def test_a_run_that_retries_is_traced_on_failure_with_both_planner_attempts(failing_run):
    assert failing_run.run.returncode == 0, failing_run.run.stdout + failing_run.run.stderr
    [path] = list(failing_run.traces.glob("*.json"))
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["failed"] is True

    planner = [n for n in doc["nodes"] if n["node"] == "ast_planner"]
    assert [n["attempt"] for n in planner] == [1, 2]
    assert len({n["sub_query_id"] for n in planner}) == 1
    first, second = (n["llm_calls"][0] for n in planner)
    assert json.loads(first["response"]["content"])["select_items"][0]["expr"]["args"][0]["column_name"] == "CustomerIdd"
    # The retry's prompt carries the validator's feedback; the first one did not.
    assert "CustomerIdd" in second["messages"][-1]["content"]
    assert "CustomerIdd" not in first["messages"][-1]["content"]

    [validator_1, _validator_2] = [n for n in doc["nodes"] if n["node"] == "logical_validator"]
    assert any(e["error_code"] == "COLUMN_NOT_FOUND" for e in validator_1["errors"] + validator_1["warnings"])
    assert [n["node"] for n in doc["nodes"] if n["llm_calls"]].count("refiner") == 1
    assert doc["result"]["sub_queries"][0]["retry_count"] == 1
    # The retry recovered, so the run succeeded; the trace still records it.
    assert doc["result"]["sub_queries"][0]["status"] == "success"
    assert doc["result"]["status"] == "success"


@pytest.mark.e2e
def test_replay_reproduces_the_run_with_zero_model_calls(failing_run):
    [path] = list(failing_run.traces.glob("*.json"))
    calls_before = len(failing_run.server.calls)

    r = run_cli(failing_run.root, failing_run.env, "trace", "replay", str(path))
    out = ANSI.sub("", r.stdout)
    assert r.returncode == 0, out + r.stderr
    assert len(failing_run.server.calls) == calls_before  # the model endpoint was never called
    assert "0 model calls" in out
    assert "5 recorded LLM calls served" in out
    assert "matches the recording" in out
    assert "Traceback" not in out + r.stderr


@pytest.mark.e2e
def test_replay_reports_where_the_run_diverged_from_the_recording(failing_run, tmp_path):
    [path] = list(failing_run.traces.glob("*.json"))
    doc = json.loads(path.read_text(encoding="utf-8"))
    for node in doc["nodes"]:
        if node["node"] == "ast_planner" and node["attempt"] == 2:
            node["llm_calls"] = []  # the recording is missing the retry's call
    edited = tmp_path / path.name
    edited.write_text(json.dumps(doc), encoding="utf-8")
    calls_before = len(failing_run.server.calls)

    r = run_cli(failing_run.root, failing_run.env, "trace", "replay", str(edited))
    out = ANSI.sub("", r.stdout)
    assert r.returncode == 1, out + r.stderr
    assert "diverged" in out.lower()
    [line] = [ln for ln in out.splitlines() if "diverged at" in ln.lower()]
    assert "ast_planner" in line and "attempt 2" in line
    assert "Traceback" not in out + r.stderr
    assert len(failing_run.server.calls) == calls_before

