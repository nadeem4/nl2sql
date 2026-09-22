"""`nl2sql benchmark retrieval` on the generated demo: no key, no LLM, the local embedder.

Report-only: recall is known to be imperfect, so this checks that the harness
runs over every answerable gold question and writes its report and record,
not what the recall is.
"""
import json
import pathlib

import pytest

from nl2sql.evaluation.gold import load_gold_dataset

from .conftest import _base_env, run_cli

REPO = pathlib.Path(__file__).resolve().parents[4]


@pytest.mark.e2e
def test_retrieval_recall_runs_over_every_answerable_question(demo_project, tmp_path):
    report_path, results = tmp_path / "retrieval.json", tmp_path / "records"
    proc = run_cli(demo_project, _base_env(), "benchmark", "retrieval", "--export-path", str(report_path),
                   "--record", "--note", "e2e", "--results-dir", str(results), timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    report = json.loads(report_path.read_text(encoding="utf-8"))
    answerable = {q.id for q in load_gold_dataset() if q.needed_tables}
    assert {r["id"] for r in report["results"]} == answerable
    assert report["settings"]["full_snapshot_max_tables"] == 0 and report["settings"]["embedding"]
    # The demo registers three datasources, so `retrieval_recall._datasource`
    # picks the top vector hit rather than the only one. The gold set is
    # Chinook-only and all but one question still lands on Chinook; "Who does
    # Jane Peacock report to?" ranks the support desk first, which has agents.
    # That is a property of this harness, not of the pipeline: the resolver
    # passes every candidate on, so a real run is unaffected. Pinning the gold
    # benchmark to Chinook belongs in `nl2sql/evaluation/retrieval_recall.py`.
    registered = {"chinook", "support", "webanalytics"}
    for r in report["results"]:
        assert 0.0 <= r["table_recall"] <= 1.0 and 0.0 <= r["column_recall"] <= 1.0
        assert r["datasource_id"] in registered
    on_chinook = sum(1 for r in report["results"] if r["datasource_id"] == "chinook")
    assert on_chinook >= len(report["results"]) - 2
    # Retrieval ran: with 11 tables and k=8, not every question was sent the full schema.
    assert min(r["tables_sent"] for r in report["results"]) < 11
    assert report["summary"]["questions"] == len(answerable)
    assert "Schema retrieval recall" in proc.stdout and "table recall" in proc.stdout

    # `records.describe_database` names every registered datasource, so the
    # record now files itself under the combined name rather than "chinook".
    # That means a fresh run no longer continues the committed
    # `benchmarks/retrieval/chinook/` series -- the same follow-up in
    # `nl2sql/evaluation/records.py` as noted above.
    [record] = list(results.glob("retrieval/chinook-support-webanalytics/*_retrieval.json"))
    body = json.loads(record.read_text(encoding="utf-8"))
    assert body["metrics"] == report["summary"] and body["note"] == "e2e"
    assert body["database"] == report["database"]
    baseline = REPO / "benchmarks" / "retrieval" / "chinook" / "2026-09-22_571cd16_retrieval.json"
    assert json.loads(baseline.read_text(encoding="utf-8"))["database"]["datasource_id"] == "chinook"


@pytest.mark.e2e
def test_the_report_lands_in_the_project_folder_by_default(demo_project):
    report = demo_project / "benchmark_retrieval.json"
    report.unlink(missing_ok=True)
    proc = run_cli(demo_project, _base_env(), "benchmark", "retrieval", "--questions", "chinook_001", timeout=900)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(report.read_text(encoding="utf-8"))["summary"]["questions"] == 1
