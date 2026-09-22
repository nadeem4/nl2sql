"""The plan cache end to end, through the real CLI and the fake LLM.

Each test gets a private copy of the demo's schema store (``SCHEMA_STORE_PATH``),
so a plan cached here is never seen by another test, whatever the order. The
e2e conftest turns the cache off for every other test.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from nl2sql.schema import SqliteSchemaStore

from .conftest import _base_env, run_cli
from .recordings_chinook import RULES_COUNT_CUSTOMERS, RULES_TOP_CUSTOMERS
from .test_trace_fake_llm import _serve

COUNT = "How many customers are there?"
TOP = "Who are the top customers by total spend?"


@pytest.fixture
def store(demo_project, tmp_path):
    """A private copy of the demo's schema store (snapshots included, no cached plans)."""
    source = sqlite3.connect(demo_project / "data" / "schema_store.db")
    path = tmp_path / "schema_store.db"
    target = sqlite3.connect(path)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    copy = SqliteSchemaStore(path=path)
    copy.clear_plan_cache()
    copy.close()
    return path


def _env(store, trace_dir, enabled="true", policies=None):
    env = _base_env()
    if policies:
        env["POLICIES_CONFIG"] = str(policies)
    env.update({"OPENAI_API_KEY": "sk-" + "fake-plan-cache", "TRACE_MODE": "always", "TRACE_DIR": str(trace_dir),
                "SCHEMA_STORE_PATH": str(store), "PLAN_CACHE_ENABLED": enabled,
                "SQL_AGENT_RETRY_BASE_DELAY_SEC": "0", "SQL_AGENT_RETRY_JITTER_SEC": "0"})
    return env


def _ask(demo_project, tmp_path, store, n, rules, question, role="admin", enabled="true", policies=None):
    """One CLI run; returns (planner calls made, the trace document, the CLI result)."""
    config = f"llm.plan-cache-{tmp_path.name}-{n}.yaml"
    server = _serve(demo_project, rules, config)
    trace_dir = tmp_path / f"traces-{n}"
    try:
        r = run_cli(demo_project, _env(store, trace_dir, enabled, policies), "run", "--role", role,
                    "--llm-config", f"configs/{config}", question)
    finally:
        server.stop()
    paths = list(trace_dir.glob("*.json"))
    assert len(paths) == 1, r.stdout + r.stderr
    [path] = paths
    doc = json.loads(path.read_text(encoding="utf-8"))
    planner_calls = [c for c in server.calls if c["name"] == "PlanModel"]
    return planner_calls, doc, r


def _sub(doc):
    [sub] = doc["result"]["sub_queries"]
    return sub


def _codes(doc):
    return [e["error_code"] for e in doc["result"]["errors"]]


@pytest.mark.e2e
def test_asking_twice_makes_no_second_planner_call_and_returns_the_same_sql_and_rows(demo_project, tmp_path, store):
    first_calls, first, r1 = _ask(demo_project, tmp_path, store, 1, RULES_COUNT_CUSTOMERS, COUNT)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert len(first_calls) == 1
    assert _sub(first)["plan_source"] == "llm"

    second_calls, second, r2 = _ask(demo_project, tmp_path, store, 2, RULES_COUNT_CUSTOMERS, COUNT)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert second_calls == []

    a, b = _sub(first), _sub(second)
    assert b["plan_source"] == "cache"
    assert b["sql"] == a["sql"] and b["sql"]
    assert b["rows"] == a["rows"] and b["rows"]["rows"]
    # The cached plan is still validated, generated and executed.
    assert [c["passed"] for c in b["validation"]] and all(c["passed"] for c in b["validation"])
    nodes = {n["node"] for n in second["nodes"]}
    assert {"logical_validator", "generator", "executor"} <= nodes
    # Telemetry and trace: a hit, and no planner tokens.
    assert second["result"]["usage"]["plan_cache_hits"] == 1
    assert "ast_planner" not in second["result"]["usage"]["nodes"]
    [planner] = [n for n in second["nodes"] if n["node"] == "ast_planner"]
    assert planner["llm_calls"] == []
    assert planner["outputs"]["ast_planner_response"]["plan_source"] == "cache"


@pytest.mark.e2e
def test_a_plan_cached_for_admin_is_refused_for_viewer(demo_project, tmp_path, store):
    calls, _doc, r = _ask(demo_project, tmp_path, store, 1, RULES_TOP_CUSTOMERS, TOP, role="admin")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls) == 1

    calls, doc, _r = _ask(demo_project, tmp_path, store, 2, RULES_TOP_CUSTOMERS, TOP, role="viewer")

    # The viewer's planner was never called: the cached admin plan reached the
    # validator, which refused it.
    assert calls == []
    assert _sub(doc)["plan_source"] == "cache"
    assert "SECURITY_VIOLATION" in _codes(doc)
    assert _sub(doc)["sql"] == ""
    assert "executor" not in {n["node"] for n in doc["nodes"]}


@pytest.mark.e2e
def test_a_policy_change_between_runs_takes_effect_on_a_cached_plan(demo_project, tmp_path, store):
    calls, _doc, r = _ask(demo_project, tmp_path, store, 1, RULES_COUNT_CUSTOMERS, COUNT, role="analyst")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls) == 1

    policies = json.loads((demo_project / "configs" / "policies.demo.json").read_text(encoding="utf-8"))
    analyst = policies["roles"]["analyst"]
    analyst["allowed_tables"] = [t for t in analyst["allowed_tables"] if t != "chinook.Customer"]
    narrowed = tmp_path / "policies.narrowed.json"
    narrowed.write_text(json.dumps(policies), encoding="utf-8")

    calls, doc, _r = _ask(demo_project, tmp_path, store, 2, RULES_COUNT_CUSTOMERS, COUNT, role="analyst",
                          policies=narrowed)

    assert calls == []
    assert _sub(doc)["plan_source"] == "cache"
    assert "SECURITY_VIOLATION" in _codes(doc)


@pytest.mark.e2e
def test_a_refused_plan_is_not_cached(demo_project, tmp_path, store):
    _calls, doc, _r = _ask(demo_project, tmp_path, store, 1, RULES_TOP_CUSTOMERS, TOP, role="viewer")
    assert "SECURITY_VIOLATION" in _codes(doc)

    calls, doc, r = _ask(demo_project, tmp_path, store, 2, RULES_TOP_CUSTOMERS, TOP, role="admin")

    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls) == 1
    assert _sub(doc)["plan_source"] == "llm"


@pytest.mark.e2e
def test_the_disable_setting_calls_the_planner_every_time(demo_project, tmp_path, store):
    for n in (1, 2):
        calls, doc, r = _ask(demo_project, tmp_path, store, n, RULES_COUNT_CUSTOMERS, COUNT, enabled="false")
        assert r.returncode == 0, r.stdout + r.stderr
        assert len(calls) == 1
        assert _sub(doc)["plan_source"] == "llm"


@pytest.mark.e2e
def test_cache_clear_makes_the_next_run_call_the_planner(demo_project, tmp_path, store):
    _ask(demo_project, tmp_path, store, 1, RULES_COUNT_CUSTOMERS, COUNT)

    cleared = run_cli(demo_project, _env(store, tmp_path / "unused"), "cache", "clear")
    assert cleared.returncode == 0, cleared.stdout + cleared.stderr
    assert "Cleared 1 cached plans" in " ".join(cleared.stdout.split())

    calls, doc, r = _ask(demo_project, tmp_path, store, 2, RULES_COUNT_CUSTOMERS, COUNT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls) == 1
    assert _sub(doc)["plan_source"] == "llm"
