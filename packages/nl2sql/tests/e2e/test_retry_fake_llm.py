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
