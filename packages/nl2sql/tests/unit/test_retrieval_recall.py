"""Retrieval recall's parts: per-case recall, routing's ranking, mode retrieval's runtime and the records.

The run against the indexed demo with the local embedder is
``tests/e2e/test_evals_retrieval.py``.
"""
import datetime as dt
from types import SimpleNamespace

import pytest

from nl2sql.evaluation import records
from nl2sql.evaluation.scorers import retrieval_recall as rr
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, load_suite
from nl2sql.testing.runtime import fake_runtime

WHEN = dt.datetime(2026, 9, 21, 14, 5, 6, tzinfo=dt.timezone.utc)


CHINOOK_002 = next(c for c in load_suite(CHINOOK_SUITE_PATH).cases if c.id == "chinook_002")


def test_recall_is_the_share_of_needed_tables_and_columns_sent_to_the_planner():
    case = CHINOOK_002  # Customer and Invoice; five columns
    assert case.tables == ["Customer", "Invoice"]
    scored = rr.recall(case, {"Customer": ["CustomerId", "FirstName", "Country"], "Track": ["Name"]})
    assert scored["table_recall"] == 0.5 and scored["missed_tables"] == ["Invoice"]
    assert scored["column_recall"] == pytest.approx(2 / 5)
    assert scored["missed_columns"] == ["Customer.LastName", "Invoice.CustomerId", "Invoice.Total"]
    assert scored["tables_sent"] == 2 and scored["columns_sent"] == 4


def test_names_match_whatever_their_case():
    scored = rr.recall(CHINOOK_002, {"customer": ["customerid", "firstname", "lastname"],
                                     "INVOICE": ["CustomerId", "Total"]})
    assert scored["table_recall"] == 1.0 and scored["column_recall"] == 1.0 and scored["missed_columns"] == []


def test_routing_ranks_datasources_best_first_up_to_the_limit_with_the_routing_settings():
    from nl2sql.config.models import Settings
    from nl2sql.indexing.models import RouteCandidate

    asked = []

    def route(question, ids, explain=None, *, limit, floor_ratio):
        asked.append((list(ids), limit, floor_ratio))
        return [RouteCandidate(datasource_id=d, score=1.0) for d in ("support", "chinook", "webanalytics")]

    ctx = SimpleNamespace(ds_registry=SimpleNamespace(list_ids=lambda: ["chinook", "support", "webanalytics"]),
                          settings=Settings.model_validate({"routing": {"candidates": 4, "floor_ratio": 0.2}}),
                          vector_store=SimpleNamespace(route=route))
    assert rr.routed(ctx, "q", 2) == ["support", "chinook"]
    assert asked == [(["chinook", "support", "webanalytics"], 4, 0.2)]
    one = SimpleNamespace(ds_registry=SimpleNamespace(list_ids=lambda: ["chinook"]))
    assert rr.routed(one, "q", 3) == ["chinook"]


def test_mode_retrieval_asks_the_retriever_about_the_cases_datasource_with_the_search_forced(monkeypatch):
    """The retriever is asked about the case's datasource, whatever routing ranks first."""
    from nl2sql.evaluation import run as run_module
    from nl2sql.evaluation.suite import EvalSuite
    from nl2sql.registry import Registry

    asked = []

    def sent(run, case, role):
        asked.append((case.id, case.datasources[0], role, run.settings.retrieval.full_snapshot_max_tables,
                      run.settings.retrieval.table_selector))
        return {"Customer": ["CustomerId", "FirstName", "LastName"], "Invoice": ["CustomerId", "Total"]}

    monkeypatch.setattr(rr, "sent", sent)
    monkeypatch.setattr(rr, "routed", lambda run, question, limit: ["support", "chinook"][:limit])
    ctx = fake_runtime(registry=Registry.default())
    suite = EvalSuite(cases=[CHINOOK_002.model_copy(update={"outcome": {"admin": "allowed"}})])
    report = run_module.run_evals(ctx, [suite], mode="retrieval", scorers=["retrieval_recall", "routing"],
                                  curated=True)

    # No model runs in mode retrieval, so the table selector is off for it (C-W4) and on for the engine.
    assert asked == [("chinook_002", "chinook", "admin", 0, False)]
    assert ctx.settings.retrieval.full_snapshot_max_tables == 15 and ctx.settings.retrieval.table_selector is True
    board = report.configs["retrieval"]
    assert board["retrieval"]["column_recall"] == 1.0
    # Shortlisted, but ranked second: routing's top-1 is part of the verdict.
    assert board["scores"]["routing"]["failed"] == 1
    assert board["routing"] == {"cases": 1, "top1": 0.0, "shortlist": 1.0}


def test_the_settings_a_retrieval_report_names_say_the_selector_was_off():
    ctx = fake_runtime(vector_store=SimpleNamespace(FETCH_MULTIPLIER=4, LAMBDA_MULT=0.7)).derive(rr.RETRIEVAL_CHANGES)

    assert rr.retrieval_settings(ctx)["table_selector"] is False


def _report(table=0.9, column=0.6):
    """A mode retrieval ``EvalReport``: one case, the recall as given."""
    from nl2sql.evaluation import report

    detail = {"table_recall": table, "column_recall": column, "tables_sent": 4.5, "columns_sent": 20.1}
    scores = {"retrieval_recall": {"passed": False, "value": table, "detail": detail}}
    status, _ = report.case_status(scores)
    run = {"id": "chinook_001", "role": "admin", "pass": 1, "expected": "allowed", "status": status,
           "lenient_status": status, "scores": scores, "cost": 0.0, "latency_s": 0.1}
    board = report.score_config([run], passes=1)
    board.update(planned_cases=1, completed_cases=1)
    suite = load_suite(CHINOOK_SUITE_PATH)
    return report.EvalReport(
        mode="retrieval", suites=[{"name": suite.name, "path": str(suite.path), "digest": suite.digest()}],
        configs={"retrieval": board},
        settings={"retrieval": {"table_k": 8, "planning_k": 12, "fetch_multiplier": 4, "lambda_mult": 0.7,
                                "full_snapshot_max_tables": 0, "query": "question",
                                "embedding": "local:all-MiniLM-L6-v2"}})


def test_a_retrieval_record_holds_the_run_the_settings_and_the_means(tmp_path):
    [path] = records.write_records(_report(), tmp_path, recorded_at=WHEN, engine_version="0.9.0",
                                   code={"git_commit": "abc1234def", "git_dirty": False})
    assert path.relative_to(tmp_path).as_posix() == "retrieval/unknown/2026-09-21_abc1234_retrieval.json"
    [rec] = records.load_records(tmp_path)
    assert rec["kind"] == "retrieval" and rec["metrics"]["table_recall"] == 0.9
    assert rec["settings"]["table_k"] == 8 and rec["dataset"]["name"] == "chinook"
    assert rec["settings"]["embedding"] == "local:all-MiniLM-L6-v2"


def test_publish_adds_a_retrieval_section_newest_first(tmp_path):
    for when, recall in ((WHEN.replace(day=20), 0.8), (WHEN, 0.9)):
        records.write_records(_report(recall), tmp_path / "r", recorded_at=when, engine_version="0.9.0",
                              code={})
    page = records.render_history(records.load_records(tmp_path / "r"))
    section = page.split("## Retrieval recall")[1]
    assert section.index("90.0%") < section.index("80.0%")
    assert "k 8 tables / 12 planning" in section
    assert "## Retrieval recall" not in records.render_history([])
