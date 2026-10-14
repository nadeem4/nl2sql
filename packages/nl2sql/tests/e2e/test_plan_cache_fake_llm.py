"""The plan cache end to end, through the real CLI and the fake LLM.

Each test gets a private copy of the demo's index and state store (its own
``storage.path``), so a plan cached here is never seen by another test,
whatever the order. Every other test's config turns the cache off.
"""
from __future__ import annotations

import json

import pytest
import yaml

from nl2sql.schema import SqliteSchemaStore

from .conftest import _base_env, private_storage, run_cli
from .recordings_chinook import RULES_COUNT_CUSTOMERS, RULES_TOP_CUSTOMERS
from .test_trace_fake_llm import _serve

COUNT = "How many customers are there?"
TOP = "Who are the top customers by total spend?"


@pytest.fixture
def store(demo_project, tmp_path):
    """A private copy of the demo's index and state store (snapshots included, no cached plans)."""
    path = private_storage(demo_project, tmp_path / "store")
    copy = SqliteSchemaStore(path=path / "state.db")
    copy.clear_plan_cache()
    copy.close()
    return path


def _env():
    env = _base_env()
    env["OPENAI_API_KEY"] = "sk-" + "fake-plan-cache"
    return env


def _config_name(tmp_path, n):
    return f"plan-cache-{tmp_path.name}-{n}.yaml"


def _ask(demo_project, tmp_path, store, n, rules, question, role="admin", enabled=True, policies=None,
         today=None):
    """One CLI run; returns (planner calls made, the trace document, the CLI result)."""
    config = _config_name(tmp_path, n)
    pipeline = {"plan_cache": enabled, **({"today": today} if today else {})}
    server = _serve(demo_project, rules, config, mode="always", settings={"pipeline": pipeline},
                    policies=policies)
    # Point the config at the test's own store, not a fresh copy of the demo's.
    path = demo_project / config
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["storage"] = {"path": str(store)}
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    trace_dir = store / "traces"
    for old in trace_dir.glob("*.json"):
        old.unlink()
    try:
        r = run_cli(demo_project, _env(), "run", "--role", role, question, config=config)
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

    policies = yaml.safe_load((demo_project / "policies.yaml").read_text(encoding="utf-8"))
    analyst = policies["roles"]["analyst"]
    analyst["allowed_tables"] = [t for t in analyst["allowed_tables"] if t != "chinook.Customer"]
    narrowed = policies

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
        calls, doc, r = _ask(demo_project, tmp_path, store, n, RULES_COUNT_CUSTOMERS, COUNT, enabled=False)
        assert r.returncode == 0, r.stdout + r.stderr
        assert len(calls) == 1
        assert _sub(doc)["plan_source"] == "llm"


@pytest.mark.e2e
def test_cache_clear_makes_the_next_run_call_the_planner(demo_project, tmp_path, store):
    _ask(demo_project, tmp_path, store, 1, RULES_COUNT_CUSTOMERS, COUNT)

    cleared = run_cli(demo_project, _env(), "cache", "clear", config=_config_name(tmp_path, 1))
    assert cleared.returncode == 0, cleared.stdout + cleared.stderr
    assert "Cleared 1 cached plans" in " ".join(cleared.stdout.split())

    calls, doc, r = _ask(demo_project, tmp_path, store, 2, RULES_COUNT_CUSTOMERS, COUNT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls) == 1
    assert _sub(doc)["plan_source"] == "llm"


@pytest.mark.e2e
def test_a_plan_cached_on_one_day_is_planned_again_the_next_day(demo_project, tmp_path, store):
    # The planner turns "this year" or "the last 30 days" into literal dates
    # from the run date, so a plan from another day may answer the wrong window.
    calls, doc, r = _ask(demo_project, tmp_path, store, 1, RULES_COUNT_CUSTOMERS, COUNT, today="2013-12-31")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls) == 1

    calls, doc, r = _ask(demo_project, tmp_path, store, 2, RULES_COUNT_CUSTOMERS, COUNT, today="2013-12-31")
    assert r.returncode == 0, r.stdout + r.stderr
    assert calls == []
    assert _sub(doc)["plan_source"] == "cache"

    calls, doc, r = _ask(demo_project, tmp_path, store, 3, RULES_COUNT_CUSTOMERS, COUNT, today="2014-01-01")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(calls) == 1
    assert _sub(doc)["plan_source"] == "llm"
