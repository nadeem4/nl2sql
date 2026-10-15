"""``nl2sql feedback``: list, stats, export --good and clear over the feedback table."""
from __future__ import annotations

import hashlib
import json
import re

import yaml
from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, load_suite
from nl2sql.feedback import rated_row
from nl2sql.storage.sqlite import SqliteState
from nl2sql.tracing.document import TRACE_FORMAT_VERSION

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _record(trace_id, question, sql, codes=(), sub_queries=1):
    return {"trace_id": trace_id, "question": question, "role": "admin", "status": "success",
            "sql": list(sql), "error_codes": list(codes), "retries": 0, "validator_failures": 0,
            "plan_cache_hits": 0, "sub_queries": sub_queries, "models": {}, "engine_version": "x"}


def _seed(stores):
    path = stores.state
    trace_dir = stores.traces
    trace_dir.mkdir(parents=True)
    store = SqliteState(path)
    store.migrate()
    store.add_feedback(rated_row(_record("a1", "How many customers are there?", ["SELECT COUNT(*) AS n FROM Customer"]),
                                 "up", None))
    store.add_feedback(rated_row(_record("a2", "Which genre sells most?", ["SELECT 1", "SELECT 2"], sub_queries=2),
                                 "up", None))
    store.add_feedback(rated_row(_record("a3", "Who earns most?", [], codes=["SECURITY_VIOLATION"], sub_queries=0),
                                 "down", "wrong table"))
    store.close()
    trace = {"trace_format_version": TRACE_FORMAT_VERSION, "trace_id": "t1", "nodes": [],
             "result": {"errors": [{"error_code": "QUESTION_NOT_ANSWERABLE"}], "sub_queries": []}}
    (trace_dir / "20260921T120000000000Z_t1.json").write_text(json.dumps(trace), encoding="utf-8")
    return path


def _out(result):
    return ANSI.sub("", result.output)


def test_stats_prints_the_rates(tmp_path, project_stores):
    _seed(project_stores)

    result = runner.invoke(app, ["feedback", "stats"])

    out = _out(result)
    assert result.exit_code == 0, out
    assert "4 runs" in out and "3 rated" in out
    assert "SECURITY_VIOLATION" in out and "QUESTION_NOT_ANSWERABLE" in out
    assert "Plan cache" in out


def test_stats_as_json(tmp_path, project_stores):
    _seed(project_stores)

    result = runner.invoke(app, ["feedback", "stats", "--json"])

    stats = json.loads(result.stdout)
    assert stats["runs"] == 4
    assert stats["feedback"]["up"] == 2 and stats["feedback"]["down"] == 1


def test_list_prints_the_ratings(tmp_path, project_stores):
    _seed(project_stores)

    out = _out(runner.invoke(app, ["feedback", "list"]))

    assert "How many customers are there?" in out and "wrong table" in out


def test_export_good_writes_draft_cases_and_never_touches_a_packaged_suite(tmp_path, project_stores):
    _seed(project_stores)
    before = hashlib.sha256(CHINOOK_SUITE_PATH.read_bytes()).hexdigest()
    target = tmp_path / "drafts.yaml"

    result = runner.invoke(app, ["feedback", "export", "--good", "--out", str(target)])

    out = _out(result)
    assert result.exit_code == 0, out
    assert hashlib.sha256(CHINOOK_SUITE_PATH.read_bytes()).hexdigest() == before
    # One draft: the other thumbs-up run has two statements, and a case's sql is one.
    [case] = load_suite(target).cases
    assert case.question == "How many customers are there?"
    assert case.sql.strip() == "SELECT COUNT(*) AS n FROM Customer"
    assert (case.status, case.origin, case.tags, case.rows) == ("draft", "feedback", [], None)
    assert "review" in target.read_text(encoding="utf-8").lower()
    assert "1 draft" in out and "skipped 1" in out.lower()
    assert "status: approved" in out and "engine.evals.regenerate" in out


def test_export_refuses_to_write_over_a_packaged_suite(tmp_path, project_stores):
    _seed(project_stores)
    before = hashlib.sha256(CHINOOK_SUITE_PATH.read_bytes()).hexdigest()

    result = runner.invoke(app, ["feedback", "export", "--good", "--out", str(CHINOOK_SUITE_PATH)])

    assert result.exit_code != 0
    assert hashlib.sha256(CHINOOK_SUITE_PATH.read_bytes()).hexdigest() == before


def test_export_needs_good(tmp_path, project_stores):
    _seed(project_stores)

    assert runner.invoke(app, ["feedback", "export", "--out", str(tmp_path / "d.yaml")]).exit_code != 0


def test_clear_removes_the_ratings(tmp_path, project_stores):
    path = _seed(project_stores)

    result = runner.invoke(app, ["feedback", "clear", "--yes"])

    assert result.exit_code == 0, _out(result)
    assert "Cleared 3" in _out(result)
    store = SqliteState(path)
    store.migrate()
    assert store.list_feedback() == []
    store.close()


def test_commands_on_a_project_with_no_feedback_report_nothing(project_stores):
    listed = runner.invoke(app, ["feedback", "list"])
    cleared = runner.invoke(app, ["feedback", "clear", "--yes"])
    stats = runner.invoke(app, ["feedback", "stats"])

    assert (listed.exit_code, cleared.exit_code, stats.exit_code) == (0, 0, 0)
    assert "No feedback recorded" in _out(listed)
    assert "Cleared 0" in _out(cleared)
    assert "0 runs" in _out(stats)
