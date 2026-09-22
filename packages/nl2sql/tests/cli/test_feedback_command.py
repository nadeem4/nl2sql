"""``nl2sql feedback``: list, stats, export --good and clear over the feedback table."""
from __future__ import annotations

import hashlib
import json
import re

import yaml
from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.common.settings import settings
from nl2sql.evaluation.gold import GOLD_DATASET_PATH, GoldQuestion
from nl2sql.feedback import FeedbackStore
from nl2sql.tracing.document import TRACE_FORMAT_VERSION

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _record(trace_id, question, sql, codes=(), sub_queries=1):
    return {"trace_id": trace_id, "question": question, "role": "admin", "status": "success",
            "sql": list(sql), "error_codes": list(codes), "retries": 0, "validator_failures": 0,
            "plan_cache_hits": 0, "sub_queries": sub_queries, "models": {}, "engine_version": "x"}


def _seed(tmp_path, monkeypatch):
    path = tmp_path / "data" / "schema_store.db"
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    monkeypatch.setattr(settings, "schema_store_path", str(path))
    monkeypatch.setattr(settings, "trace_dir", str(trace_dir))
    store = FeedbackStore(path)
    store.save(_record("a1", "How many customers are there?", ["SELECT COUNT(*) AS n FROM Customer"]),
               rating="up", note=None)
    store.save(_record("a2", "Which genre sells most?", ["SELECT 1", "SELECT 2"], sub_queries=2),
               rating="up", note=None)
    store.save(_record("a3", "Who earns most?", [], codes=["SECURITY_VIOLATION"], sub_queries=0),
               rating="down", note="wrong table")
    store.close()
    trace = {"trace_format_version": TRACE_FORMAT_VERSION, "trace_id": "t1", "nodes": [],
             "result": {"errors": [{"error_code": "QUESTION_NOT_ANSWERABLE"}], "sub_queries": []}}
    (trace_dir / "20260921T120000000000Z_t1.json").write_text(json.dumps(trace), encoding="utf-8")
    return path


def _out(result):
    return ANSI.sub("", result.output)


def test_stats_prints_the_rates(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    result = runner.invoke(app, ["feedback", "stats"])

    out = _out(result)
    assert result.exit_code == 0, out
    assert "4 runs" in out and "3 rated" in out
    assert "SECURITY_VIOLATION" in out and "QUESTION_NOT_ANSWERABLE" in out
    assert "Plan cache" in out


def test_stats_as_json(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    result = runner.invoke(app, ["feedback", "stats", "--json"])

    stats = json.loads(result.stdout)
    assert stats["runs"] == 4
    assert stats["feedback"]["up"] == 2 and stats["feedback"]["down"] == 1


def test_list_prints_the_ratings(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = _out(runner.invoke(app, ["feedback", "list"]))

    assert "How many customers are there?" in out and "wrong table" in out


def test_export_good_writes_draft_gold_entries_and_never_touches_the_gold_set(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    before = hashlib.sha256(GOLD_DATASET_PATH.read_bytes()).hexdigest()
    target = tmp_path / "drafts.yaml"

    result = runner.invoke(app, ["feedback", "export", "--good", "--out", str(target)])

    out = _out(result)
    assert result.exit_code == 0, out
    assert hashlib.sha256(GOLD_DATASET_PATH.read_bytes()).hexdigest() == before
    drafts = yaml.safe_load(target.read_text(encoding="utf-8"))
    # One draft: the other thumbs-up run has two statements, and gold_sql is one.
    assert len(drafts) == 1
    entry = GoldQuestion.model_validate(drafts[0])
    assert entry.question == "How many customers are there?"
    assert entry.gold_sql.strip() == "SELECT COUNT(*) AS n FROM Customer"
    assert entry.tags == [] and entry.gold_result is None
    assert "review" in target.read_text(encoding="utf-8").lower()
    assert "1 draft" in out and "skipped 1" in out.lower()


def test_export_refuses_to_write_over_the_gold_set(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    before = hashlib.sha256(GOLD_DATASET_PATH.read_bytes()).hexdigest()

    result = runner.invoke(app, ["feedback", "export", "--good", "--out", str(GOLD_DATASET_PATH)])

    assert result.exit_code != 0
    assert hashlib.sha256(GOLD_DATASET_PATH.read_bytes()).hexdigest() == before


def test_export_needs_good(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    assert runner.invoke(app, ["feedback", "export", "--out", str(tmp_path / "d.yaml")]).exit_code != 0


def test_clear_removes_the_ratings(tmp_path, monkeypatch):
    path = _seed(tmp_path, monkeypatch)

    result = runner.invoke(app, ["feedback", "clear", "--yes"])

    assert result.exit_code == 0, _out(result)
    assert "Cleared 3" in _out(result)
    store = FeedbackStore(path)
    assert store.list() == []
    store.close()


def test_commands_without_a_store_file_create_nothing(tmp_path, monkeypatch):
    path = tmp_path / "data" / "schema_store.db"
    monkeypatch.setattr(settings, "schema_store_path", str(path))
    monkeypatch.setattr(settings, "trace_dir", str(tmp_path / "traces"))

    for args in (["feedback", "list"], ["feedback", "clear", "--yes"], ["feedback", "stats"]):
        assert runner.invoke(app, args).exit_code == 0
    assert not path.exists()
