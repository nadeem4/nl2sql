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

from nl2sql.testing.fake_llm import FakeLLMServer, Rule

from .conftest import _base_env, fake_llm_section, private_storage, project_config, run_cli
from .recordings_chinook import (
    ANSWERABLE,
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
    "datasource_resolver", "decomposer", "layer_router", "sql_agent",
    "schema_retriever", "ast_planner", "logical_validator", "generator", "executor",
    "aggregator", "answer_synthesizer",
}


def _serve(demo_project, rules, name, *, store=None, mode="on_failure", settings=None, llm=None, **config):
    """Serve ``rules`` and write ``<demo>/<name>``: a config whose LLM is that server.

    Retries wait 0 s. With ``store`` (a temp folder), the run gets a private
    copy of the demo's index and state, and its traces go to ``store/traces``.
    """
    server = FakeLLMServer(rules).start()
    merged = {"pipeline": {"retries": {"base_delay_s": 0, "jitter_s": 0}}, "traces": {"mode": mode}}
    for group, values in (settings or {}).items():
        merged[group] = {**merged.get(group, {}), **values}
    project_config(demo_project, name, llm=fake_llm_section(server.base_url, **(llm or {})), settings=merged,
                   storage=private_storage(demo_project, store) if store is not None else None, **config)
    return server


def _env():
    env = _base_env()
    env["OPENAI_API_KEY"] = FAKE_KEY
    return env


def _llm_calls(doc):
    return [call for node in doc["nodes"] for call in node["llm_calls"]]


@pytest.mark.e2e
def test_a_run_writes_a_trace_with_every_node_its_llm_calls_and_no_secret(demo_project, tmp_path):
    server = _serve(demo_project, RULES_COUNT_CUSTOMERS, "trace-ok.yaml", store=tmp_path, mode="always")
    try:
        r = run_cli(demo_project, _env(), "run", QUESTION, config="trace-ok.yaml")
    finally:
        server.stop()
    assert r.returncode == 0, r.stdout + r.stderr

    [path] = list((tmp_path / "traces").glob("*.json"))
    assert path.name in ANSI.sub("", r.stdout).replace("\n", "")  # the CLI says where it went
    text = path.read_text(encoding="utf-8")
    assert FAKE_KEY not in text

    doc = json.loads(text)
    assert doc["trace_format_version"] == 1
    # The date the prompts were told, so a replay on another day pins it.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc["request"].pop("today"))
    assert doc["request"] == {"question": QUESTION, "roles": ["admin"], "tenant_id": "default_tenant",
                              "datasource_id": None, "execute": True, "clarify": True}
    assert NODES <= {n["node"] for n in doc["nodes"]}
    assert doc["llm"]["by_node"] == {"datasource_resolver": "gpt-4o", "decomposer": "gpt-4o",
                                     "ast_planner": "gpt-4o", "answer_synthesizer": "gpt-4o"}

    calls = _llm_calls(doc)
    assert [c["key"]["node"] for c in calls] == ["datasource_resolver", "decomposer", "ast_planner",
                                                 "answer_synthesizer"]
    for call in calls:
        assert call["messages"] and call["messages"][0]["content"]
        assert call["usage"]["total_tokens"] == 2  # the fake reports 1 + 1 per call
        assert call["model"].startswith("gpt-4o")
    resolver, decomposer, planner, synthesizer = calls
    assert resolver["response"]["tool_calls"][0]["name"] == "AnswerabilityResponse"
    assert resolver["parsed"]["answerable_datasource_ids"] == ["chinook"]
    assert resolver["messages"][-1]["content"].endswith(QUESTION)
    assert decomposer["response"]["tool_calls"][0]["name"] == "DecomposerResponse"
    # Every node, the planner included, answers with a tool call.
    assert planner["response"]["tool_calls"][0]["name"] == "PlannerAnswer"
    assert planner["response"]["tool_calls"][0]["args"]["plan"]["tables"][0]["name"] == "Customer"
    assert planner["parsed"]["plan"]["tables"][0]["name"] == "Customer"
    assert "How many customers" in planner["messages"][-1]["content"]  # the question is in the last (human) message
    assert synthesizer["parsed"]["summary"] == "There are 59 customers."

    [sub] = doc["result"]["sub_queries"]
    assert sub["rows"]["rows"] == [[59]]
    assert doc["failed"] is False

    # Retrieval: the demo registers three datasources, so the single-datasource
    # shortcut no longer applies and routing runs for real: one search per
    # signal, then the fused ranking. Chinook ranks first for a question about customers.
    [resolver] = [n for n in doc["nodes"] if n["node"] == "datasource_resolver"]
    resolver_retrieval = resolver["outputs"]["retrieval"]
    assert resolver_retrieval["skipped"] is False
    assert resolver_retrieval["query"] == QUESTION
    searches = {s["search"]: s for s in resolver_retrieval["searches"]}
    assert {entry["datasource_id"] for entry in searches["route.description"]["pool"]} == {
        "chinook", "support", "webanalytics"}
    assert searches["routing"]["picks"][0] == "chinook"

    # The schema retriever still skips: Chinook alone is 11 tables, under its threshold.
    [retriever] = [n for n in doc["nodes"] if n["node"] == "schema_retriever"]
    retrieval = retriever["outputs"]["retrieval"]
    assert retrieval["skipped"] is True and "11 tables" in retrieval["reason"]
    assert len(retrieval["tables"]) == 11


def _retry_rules():
    """The first plan counts a column that does not exist; the second is right."""
    bad_plan = copy.deepcopy(COUNT_CUSTOMERS_PLAN)
    bad_plan["select_items"][0]["expr"]["args"][0]["column_name"] = "CustomerIdd"
    plans = []

    def plan(_text):
        plans.append(1)
        return bad_plan if len(plans) == 1 else COUNT_CUSTOMERS_PLAN

    return [
        ANSWERABLE,
        Rule("DecomposerResponse", COUNT_CUSTOMERS_DECOMPOSER),
        Rule("PlannerAnswer", plan),
        Rule("AggregatedResponse", count_customers_answer),
        Rule("plain", "Count Customer.CustomerId; CustomerIdd does not exist."),
    ]


@pytest.fixture(scope="module")
def failing_run(demo_project, tmp_path_factory):
    store = tmp_path_factory.mktemp("store")
    server = _serve(demo_project, _retry_rules(), "trace-retry.yaml", store=store)
    env = _env()
    run = run_cli(demo_project, env, "run", QUESTION, config="trace-retry.yaml")
    try:
        yield SimpleNamespace(root=demo_project, server=server, env=env, run=run, traces=store / "traces")
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
    assert first["response"]["tool_calls"][0]["args"]["plan"]["select_items"][0]["expr"]["args"][0]["column_name"] == "CustomerIdd"
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

    r = run_cli(failing_run.root, failing_run.env, "trace", "replay", str(path), config="trace-retry.yaml")
    out = ANSI.sub("", r.stdout)
    assert r.returncode == 0, out + r.stderr
    assert len(failing_run.server.calls) == calls_before  # the model endpoint was never called
    assert "0 model calls" in out
    assert "6 recorded LLM calls served" in out
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

    r = run_cli(failing_run.root, failing_run.env, "trace", "replay", str(edited), config="trace-retry.yaml")
    out = ANSI.sub("", r.stdout)
    assert r.returncode == 1, out + r.stderr
    assert "diverged" in out.lower()
    [line] = [ln for ln in out.splitlines() if "diverged at" in ln.lower()]
    assert "ast_planner" in line and "attempt 2" in line
    assert "Traceback" not in out + r.stderr
    assert len(failing_run.server.calls) == calls_before

