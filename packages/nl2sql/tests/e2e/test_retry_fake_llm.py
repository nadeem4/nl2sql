"""The retry loop, end to end, on the failure that used to escape it.

The validator is the only gate with an edge back to the planner. A plan whose
tables are not joined used to pass it and die at the generator, so the loop
never fired: both committed benchmark records report ``retries.total == 0``
across 86 questions. Here the planner returns an unjoined plan first and the
corrected one once it has been told why, and the run finishes with one retry.
"""
from __future__ import annotations

import json

import pytest

from nl2sql.testing.fake_llm import Rule

from .conftest import _base_env, run_cli
from .recordings_chinook import RULES_ALBUMS_PER_ARTIST_RETRY
from .test_trace_fake_llm import _serve

QUESTION = "How many albums does each artist have?"


def _prompt(call) -> str:
    """Every message the client sent on one recorded call, joined."""
    return "\n".join(str(m.get("content") or "") for m in call["body"].get("messages", []))


def _env():
    env = _base_env()
    env["OPENAI_API_KEY"] = "sk-fake-retry"
    return env


@pytest.mark.e2e
def test_an_unjoined_plan_is_retried_and_the_second_plan_answers(demo_project, tmp_path):
    # Arrange
    server = _serve(demo_project, RULES_ALBUMS_PER_ARTIST_RETRY, "retry.yaml", store=tmp_path, mode="always")

    # Act
    try:
        r = run_cli(demo_project, _env(), "run", QUESTION, config="retry.yaml")
    finally:
        server.stop()

    # Assert
    assert r.returncode == 0, r.stdout + r.stderr
    [path] = list((tmp_path / "traces").glob("*.json"))
    doc = json.loads(path.read_text(encoding="utf-8"))
    [sub] = doc["result"]["sub_queries"]

    assert sub["retry_count"] == 1
    assert doc["result"]["status"] == "success", doc["result"]["errors"]
    assert "JOIN" in (sub["sql"] or "").upper()

    # Two planner calls, and the second was driven by the validator's feedback.
    planner_calls = [c for c in server.calls if c["name"] == "PlannerAnswer"]
    assert len(planner_calls) == 2
    assert "never joined to the FROM table" in _prompt(planner_calls[1])

    # The failure the planner was asked to fix is reported as a retryable
    # structural error, never as a terminal generation failure.
    codes = [e["error_code"] for e in doc["result"]["errors"]]
    assert "SQL_GEN_FAILED" not in codes


def _count_customers_plan(star_alias):
    """How many customers, counted as ``COUNT(*)`` or, with ``star_alias``, ``COUNT(t1.*)``."""
    from .recordings_chinook import COUNT_CUSTOMERS_PLAN

    star = {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
            "args": [{"kind": "column", "alias": star_alias, "column_name": "*"}]}
    return {**COUNT_CUSTOMERS_PLAN, "select_items": [{"alias": "customer_count", "expr": star}]}


def _count_customers(fake_llm, monkeypatch, demo_project, planner_rules):
    """Runs "How many customers are there?" with ``planner_rules`` as the planner; returns the result and server."""
    from nl2sql import NL2SQL, UserContext

    from .recordings_chinook import RULES_COUNT_CUSTOMERS
    from .test_result_paging_fake_llm import _env

    rules = [r for r in RULES_COUNT_CUSTOMERS if r.name != "PlannerAnswer"]
    server, env = fake_llm(planner_rules + rules)
    _env(monkeypatch, demo_project, env)
    engine = NL2SQL.from_config()
    try:
        result = engine.run_query("How many customers are there?", user_context=UserContext(roles=["analyst"]))
    finally:
        engine.close()
    return result, server


@pytest.mark.e2e
def test_a_count_star_plan_runs_first_time_without_a_retry(demo_project, fake_llm, monkeypatch):
    """The planner writes ``COUNT(*)`` as a ``*`` column; the generator must not quote it (C-W3)."""
    result, _ = _count_customers(fake_llm, monkeypatch, demo_project,
                                 [Rule("PlannerAnswer", _count_customers_plan(None))])

    [sub] = result.sub_queries
    assert result.status == "success", result.errors
    assert sub.retry_count == 0
    assert "COUNT(*)" in sub.sql.replace(" ", "").upper(), sub.sql
    assert sub.rows.rows == [[59]]


@pytest.mark.e2e
def test_a_count_of_a_qualified_star_is_refused_and_the_retry_answers(demo_project, fake_llm, monkeypatch):
    """``COUNT(t1.*)`` is never rewritten (it means something else under an outer join): it is retried."""
    result, server = _count_customers(fake_llm, monkeypatch, demo_project, [
        Rule("PlannerAnswer", _count_customers_plan(None), when="non-null column such as its key"),
        Rule("PlannerAnswer", _count_customers_plan("t1")),
    ])

    [sub] = result.sub_queries
    assert result.status == "success", result.errors
    assert sub.retry_count == 1
    assert sub.rows.rows == [[59]]
    planner_calls = [c for c in server.calls if c["name"] == "PlannerAnswer"]
    assert len(planner_calls) == 2
    assert "non-null column such as its key" in _prompt(planner_calls[1])


@pytest.mark.e2e
def test_the_refiner_runs_once_between_the_two_planner_calls(demo_project, tmp_path):
    """What the refiner actually contributes on the loop's first real firing.

    It makes one free-text LLM call and appends its answer to ``state.errors``
    as a WARNING, which the planner reads as feedback -- alongside the
    validator's own error, which reaches the planner through the same channel
    whether the refiner ran or not.
    """
    # Arrange
    server = _serve(demo_project, RULES_ALBUMS_PER_ARTIST_RETRY, "retry-refiner.yaml", store=tmp_path, mode="always")

    # Act
    try:
        r = run_cli(demo_project, _env(), "run", QUESTION, config="retry-refiner.yaml")
    finally:
        server.stop()

    # Assert
    assert r.returncode == 0, r.stdout + r.stderr
    assert [c["name"] for c in server.calls] == [
        "AnswerabilityResponse", "DecomposerResponse", "PlannerAnswer", "plain", "PlannerAnswer", "AggregatedResponse",
    ]
    prompt = _prompt([c for c in server.calls if c["name"] == "PlannerAnswer"][1])
    assert "Join Album to Artist on ArtistId." in prompt  # the refiner's wording
    assert "never joined to the FROM table" in prompt  # and the validator's own
