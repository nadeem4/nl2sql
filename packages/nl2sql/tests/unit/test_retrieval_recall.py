"""Retrieval recall's pure parts: per-question recall, the means, the comparison and the records.

The run against the indexed demo with the local embedder is
``tests/e2e/test_benchmark_retrieval.py``.
"""
import datetime as dt

import pytest

from nl2sql.evaluation import records, retrieval_recall as rr
from nl2sql.evaluation.gold import GOLD_DATASET_PATH, load_gold_dataset

WHEN = dt.datetime(2026, 9, 21, 14, 5, 6, tzinfo=dt.timezone.utc)


def _question(qid="chinook_002"):
    return next(q for q in load_gold_dataset() if q.id == qid)


def test_recall_is_the_share_of_needed_tables_and_columns_sent_to_the_planner():
    q = _question()  # Customer and Invoice; five columns
    assert q.needed_tables == ["Customer", "Invoice"]
    scored = rr.score_question(q, {"Customer": ["CustomerId", "FirstName", "Country"], "Track": ["Name"]})
    assert scored["table_recall"] == 0.5 and scored["missed_tables"] == ["Invoice"]
    assert scored["column_recall"] == pytest.approx(2 / 5)
    assert scored["missed_columns"] == ["Customer.LastName", "Invoice.CustomerId", "Invoice.Total"]
    assert scored["tables_sent"] == 2 and scored["columns_sent"] == 4


def test_names_match_whatever_their_case():
    q = _question()
    scored = rr.score_question(q, {"customer": ["customerid", "firstname", "lastname"],
                                   "INVOICE": ["CustomerId", "Total"]})
    assert scored["table_recall"] == 1.0 and scored["column_recall"] == 1.0 and scored["missed_columns"] == []


def test_the_summary_is_the_mean_over_questions_and_lists_the_worst_first():
    results = [{"id": "a", "table_recall": 1.0, "column_recall": 1.0, "tables_sent": 2, "columns_sent": 10},
               {"id": "b", "table_recall": 0.5, "column_recall": 0.25, "tables_sent": 4, "columns_sent": 6},
               {"id": "c", "table_recall": 1.0, "column_recall": 0.5, "tables_sent": 3, "columns_sent": 8}]
    summary = rr.summarize(results)
    assert summary == {"questions": 3, "table_recall": 0.8333, "column_recall": 0.5833,
                       "perfect_tables": 2, "perfect_columns": 1, "tables_sent": 3.0, "columns_sent": 8.0}
    assert [r["id"] for r in rr.worst(results, 2)] == ["b", "c"]


def test_comparing_two_reports_gives_the_deltas_and_each_question_that_moved():
    old = {"summary": {"table_recall": 0.8, "column_recall": 0.5, "tables_sent": 3.0, "columns_sent": 8.0},
           "results": [{"id": "a", "table_recall": 1.0, "column_recall": 0.5},
                       {"id": "b", "table_recall": 0.5, "column_recall": 0.5}]}
    new = {"summary": {"table_recall": 0.9, "column_recall": 0.5, "tables_sent": 3.0, "columns_sent": 9.0},
           "results": [{"id": "a", "table_recall": 1.0, "column_recall": 1.0},
                       {"id": "b", "table_recall": 0.5, "column_recall": 0.5}]}
    diff = rr.compare_reports(new, old)
    assert diff["summary"]["table_recall"] == pytest.approx(0.1)
    assert diff["summary"]["column_recall"] == 0.0 and diff["summary"]["columns_sent"] == 1.0
    assert diff["moved"] == [{"id": "a", "table_recall": [1.0, 1.0], "column_recall": [0.5, 1.0]}]


def _report(table=0.9, column=0.6):
    return {"kind": "retrieval", "dataset": str(GOLD_DATASET_PATH),
            "settings": {"table_k": 8, "planning_k": 12, "fetch_multiplier": 4, "lambda_mult": 0.7,
                         "full_snapshot_max_tables": 0, "embedding": "all-MiniLM-L6-v2"},
            "summary": {"questions": 39, "table_recall": table, "column_recall": column, "perfect_tables": 30,
                        "perfect_columns": 10, "tables_sent": 4.5, "columns_sent": 20.1},
            "results": []}


def test_a_retrieval_record_holds_the_run_the_settings_and_the_means(tmp_path):
    path = records.write_retrieval_record(_report(), tmp_path, recorded_at=WHEN, engine_version="0.9.0",
                                            git_commit="abc")
    assert path.name == "2026-09-21_0.9.0_retrieval.json"
    [rec] = records.load_records(tmp_path)
    assert rec["kind"] == "retrieval" and rec["metrics"]["table_recall"] == 0.9
    assert rec["settings"]["table_k"] == 8 and rec["dataset"]["name"] == "chinook_gold.yaml"


def test_publish_adds_a_retrieval_section_newest_first(tmp_path):
    records.write_retrieval_record(_report(0.8), tmp_path / "r", recorded_at=WHEN.replace(day=20),
                                   engine_version="0.8.0", git_commit=None)
    records.write_retrieval_record(_report(0.9), tmp_path / "r", recorded_at=WHEN, engine_version="0.9.0",
                                   git_commit=None)
    page = records.render_history([], records.load_records(tmp_path / "r"))
    section = page.split("## Retrieval recall")[1]
    assert section.index("90.0%") < section.index("80.0%")
    assert "k 8 tables / 12 planning" in section
    assert records.EMPTY in page.split("## Retrieval recall")[0]
    assert "## Retrieval recall" not in records.render_history([], [])
