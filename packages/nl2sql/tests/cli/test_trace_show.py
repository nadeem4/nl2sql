"""``nl2sql trace show``: a readable timeline of a recorded run."""
from __future__ import annotations

import json
import re

from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.common.settings import settings

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TRACE_ID = "0b8f7d2e-1111-4222-8333-944455556666"


def _node(seq, node, attempt=1, sq=None, status="ok", errors=(), calls=()):
    return {"seq": seq, "node": node, "sub_query_id": sq, "attempt": attempt, "parent": None,
            "duration_s": 0.25, "status": status, "errors": list(errors), "warnings": [],
            "llm_calls": list(calls)}


def _call(tokens):
    return {"usage": {"total_tokens": tokens, "input_tokens": tokens - 10, "output_tokens": 10}}


def _write(tmp_path):
    doc = {
        "trace_format_version": 1, "trace_id": TRACE_ID, "failed": True, "outcome": "completed",
        "request": {"question": "How many customers are there?", "roles": ["admin"], "execute": True},
        "nodes": [
            _node(1, "decomposer", calls=[_call(2150)]),
            _node(2, "ast_planner", sq="sq_abc", calls=[_call(11300)]),
            _node(3, "logical_validator", sq="sq_abc", status="warning",
                  errors=[{"message": "Column 'CustomerIdd' not found in any relevant table.",
                           "error_code": "COLUMN_NOT_FOUND", "severity": "WARNING"}]),
            _node(4, "ast_planner", attempt=2, sq="sq_abc", calls=[_call(11400)]),
            _node(5, "executor", sq="sq_abc", status="error",
                  errors=[{"message": "no such table", "error_code": "DB_EXECUTION_ERROR", "severity": "ERROR"}]),
        ],
        "result": {"status": "error"},
    }
    path = tmp_path / f"20260921T101112000000Z_{TRACE_ID}.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _show(arg):
    result = runner.invoke(app, ["trace", "show", arg])
    return result, ANSI.sub("", result.output)


def test_show_prints_each_node_attempt_duration_tokens_and_status(tmp_path):
    result, out = _show(str(_write(tmp_path)))
    assert result.exit_code == 0, out
    assert "How many customers are there?" in out
    for text in ("decomposer", "ast_planner", "logical_validator", "executor", "2,150", "11,400"):
        assert text in out
    assert "COLUMN_NOT_FOUND" in out


def test_show_flags_the_first_failing_node(tmp_path):
    _result, out = _show(str(_write(tmp_path)))
    [line] = [ln for ln in out.splitlines() if "first failure" in ln.lower()]
    assert "executor" in line


def test_show_accepts_a_trace_id_from_the_traces_directory(tmp_path, monkeypatch):
    _write(tmp_path)
    monkeypatch.setattr(settings, "trace_dir", str(tmp_path))
    result, out = _show(TRACE_ID)
    assert result.exit_code == 0, out
    assert "decomposer" in out


def test_show_explains_an_unknown_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "trace_dir", str(tmp_path))
    result, out = _show("no-such-trace")
    assert result.exit_code == 1
    assert "no-such-trace" in out


def test_a_recovered_run_points_at_the_first_warning(tmp_path):
    path = _write(tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["nodes"][-1].update(status="ok", errors=[])
    path.write_text(json.dumps(doc), encoding="utf-8")
    _result, out = _show(str(path))
    [line] = [ln for ln in out.splitlines() if ln.startswith("First warning")]
    assert "logical_validator" in line
