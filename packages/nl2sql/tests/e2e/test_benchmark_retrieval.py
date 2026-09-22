"""`nl2sql benchmark retrieval` on the generated demo: no key, no LLM, the local embedder.

Report-only: recall is known to be imperfect, so this checks that the harness
runs over every answerable gold question and writes its report and record,
not what the recall is.
"""
import json

import pytest

from nl2sql.evaluation.gold import load_gold_dataset

from .conftest import _base_env, run_cli


@pytest.mark.e2e
def test_retrieval_recall_runs_over_every_answerable_question(demo_project, tmp_path):
    report_path, results = tmp_path / "retrieval.json", tmp_path / "records"
    proc = run_cli(demo_project, _base_env(), "benchmark", "retrieval", "--export-path", str(report_path),
                   "--record", "--results-dir", str(results), timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    report = json.loads(report_path.read_text(encoding="utf-8"))
    answerable = {q.id for q in load_gold_dataset() if q.needed_tables}
    assert {r["id"] for r in report["results"]} == answerable
    assert report["settings"]["full_snapshot_max_tables"] == 0 and report["settings"]["embedding"]
    for r in report["results"]:
        assert 0.0 <= r["table_recall"] <= 1.0 and 0.0 <= r["column_recall"] <= 1.0
        assert r["datasource_id"] == "chinook"
    # Retrieval ran: with 11 tables and k=8, not every question was sent the full schema.
    assert min(r["tables_sent"] for r in report["results"]) < 11
    assert report["summary"]["questions"] == len(answerable)
    assert "Schema retrieval recall" in proc.stdout and "table recall" in proc.stdout

    [record] = list(results.glob("*_retrieval.json"))
    assert json.loads(record.read_text(encoding="utf-8"))["metrics"] == report["summary"]
