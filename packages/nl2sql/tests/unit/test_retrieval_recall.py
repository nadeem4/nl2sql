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


def test_routing_ranks_datasources_best_first_without_repeats_up_to_the_limit():
    ctx = SimpleNamespace(ds_registry=SimpleNamespace(list_ids=lambda: ["chinook", "support", "webanalytics"]),
                          vector_store=SimpleNamespace(retrieve_datasource_candidates=lambda q, k: [
                              SimpleNamespace(metadata={"datasource_id": d})
                              for d in ("support", "support", "chinook", "webanalytics")]))
    assert rr.routed(ctx, "q", 2) == ["support", "chinook"]
    one = SimpleNamespace(ds_registry=SimpleNamespace(list_ids=lambda: ["chinook"]))
    assert rr.routed(one, "q", 3) == ["chinook"]


def test_mode_retrieval_asks_the_retriever_about_the_cases_datasource_with_the_search_forced(monkeypatch):
    """The retriever is asked about the case's datasource, whatever routing ranks first."""
    from nl2sql.evaluation import run as run_module
    from nl2sql.evaluation.suite import EvalSuite
    from nl2sql.registry import Registry

    asked = []

    def sent(run, case, role):
        asked.append((case.id, case.datasources[0], role, run.settings.retrieval.full_snapshot_max_tables))
        return {"Customer": ["CustomerId", "FirstName", "LastName"], "Invoice": ["CustomerId", "Total"]}

    monkeypatch.setattr(rr, "sent", sent)
    monkeypatch.setattr(rr, "routed", lambda run, question, limit: ["support", "chinook"][:limit])
    ctx = fake_runtime(registry=Registry.default())
    suite = EvalSuite(cases=[CHINOOK_002.model_copy(update={"outcome": {"admin": "allowed"}})])
    report = run_module.run_evals(ctx, [suite], mode="retrieval", scorers=["retrieval_recall", "routing"],
                                  curated=True)

    assert asked == [("chinook_002", "chinook", "admin", 0)]
    assert ctx.settings.retrieval.full_snapshot_max_tables == 15
    board = report.configs["retrieval"]
    assert board["retrieval"]["column_recall"] == 1.0 and board["scores"]["routing"]["passed"] == 1


def _report(table=0.9, column=0.6):
    return {"kind": "retrieval", "dataset": str(CHINOOK_SUITE_PATH),
            "settings": {"table_k": 8, "planning_k": 12, "fetch_multiplier": 4, "lambda_mult": 0.7,
                         "full_snapshot_max_tables": 0, "embedding": "all-MiniLM-L6-v2"},
            "summary": {"questions": 39, "table_recall": table, "column_recall": column, "perfect_tables": 30,
                        "perfect_columns": 10, "tables_sent": 4.5, "columns_sent": 20.1},
            "results": []}


def test_a_retrieval_record_holds_the_run_the_settings_and_the_means(tmp_path):
    path = records.write_retrieval_record(_report(), tmp_path, recorded_at=WHEN, engine_version="0.9.0",
                                          code={"git_commit": "abc1234def", "git_dirty": False})
    assert path.relative_to(tmp_path).as_posix() == "retrieval/unknown/2026-09-21_abc1234_retrieval.json"
    [rec] = records.load_records(tmp_path)
    assert rec["kind"] == "retrieval" and rec["metrics"]["table_recall"] == 0.9
    assert rec["settings"]["table_k"] == 8 and rec["dataset"]["name"] == "chinook.yaml"


def test_publish_adds_a_retrieval_section_newest_first(tmp_path):
    for when, recall in ((WHEN.replace(day=20), 0.8), (WHEN, 0.9)):
        records.write_retrieval_record(_report(recall), tmp_path / "r", recorded_at=when, engine_version="0.9.0",
                                       code={})
    page = records.render_history(records.load_records(tmp_path / "r"))
    section = page.split("## Retrieval recall")[1]
    assert section.index("90.0%") < section.index("80.0%")
    assert "k 8 tables / 12 planning" in section
    assert "## Retrieval recall" not in records.render_history([])
