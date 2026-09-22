"""``BenchmarkRunner`` runs each gold question once per role and scores it.

Key-free: the pipeline call is replaced, so this checks the runner's own
bookkeeping -- which cases run, in what role, and how they are reported.
"""
from __future__ import annotations

from nl2sql.api.query_api import QueryResult
from nl2sql.evaluation.benchmark_runner import BenchmarkRunner
from nl2sql.evaluation.gold import GOLD_DATASET_PATH
from nl2sql.evaluation.types import BenchmarkConfig
from nl2sql.pipeline.nodes.validator.node import REFUSAL_MESSAGE

REFUSAL = QueryResult(status="error", errors=[{"node": "logical_validator", "message": REFUSAL_MESSAGE,
                                              "error_code": "SECURITY_VIOLATION", "severity": "CRITICAL"}])


def _runner(monkeypatch, include_ids, roles=None, run=None, before_case=None):
    config = BenchmarkConfig(dataset_path=GOLD_DATASET_PATH, include_ids=include_ids, roles=roles, iterations=1)
    runner = BenchmarkRunner(config, ctx=None, workers=1, before_case=before_case)
    calls = []

    def fake_run(question, role):
        calls.append((question.id, role))
        return run(question, role) if run else REFUSAL

    monkeypatch.setattr(runner, "_run", fake_run)
    return runner, calls


def test_each_question_runs_once_per_role(monkeypatch):
    runner, calls = _runner(monkeypatch, ["chinook_007"])
    result = runner.run_dataset()
    assert sorted(calls) == [("chinook_007", r) for r in ("admin", "analyst", "viewer")]
    by_role = {r["role"]: r for r in result.results}
    # REFUSAL is right for analyst and viewer, wrong for admin.
    assert by_role["analyst"]["status"] == "pass" and by_role["viewer"]["status"] == "pass"
    assert by_role["admin"]["status"] == "fail"
    assert result.metrics["by_role"]["admin"]["fail"] == 1


def test_roles_can_be_narrowed(monkeypatch):
    runner, calls = _runner(monkeypatch, ["chinook_007"], roles=["viewer"])
    runner.run_dataset()
    assert calls == [("chinook_007", "viewer")]


NOT_ANSWERABLE = QueryResult(status="error", errors=[{
    "node": "datasourceresolver", "error_code": "QUESTION_NOT_ANSWERABLE", "severity": "ERROR",
    "message": "This question can't be answered from the connected data (chinook)."}])


def test_unanswerable_questions_run_and_pass_on_the_resolvers_refusal(monkeypatch):
    seen = []
    runner, calls = _runner(monkeypatch, ["chinook_040"], run=lambda _q, _r: NOT_ANSWERABLE,
                            before_case=lambda q: seen.append(q.id))
    result = runner.run_dataset()
    assert sorted(calls) == [("chinook_040", r) for r in ("admin", "analyst", "viewer")]
    assert seen == ["chinook_040"] * 3
    assert {r["status"] for r in result.results} == {"pass"}


def test_a_crashing_run_is_a_failure_not_an_abort(monkeypatch):
    def boom(_q, _r):
        raise RuntimeError("kaput")

    runner, _ = _runner(monkeypatch, ["chinook_007"], roles=["admin"], run=boom)
    (row,) = runner.run_dataset().results
    assert row["status"] == "fail" and "kaput" in row["reason"]


def test_before_case_sees_each_question_before_it_runs(monkeypatch):
    seen = []
    runner, calls = _runner(monkeypatch, ["chinook_035", "chinook_036"], roles=["admin"],
                            before_case=lambda q: seen.append((q.id, len(calls))))
    runner.run_dataset()
    assert seen == [("chinook_035", 0), ("chinook_036", 1)]
