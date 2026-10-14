"""``nl2sql doctor``: the install, the provider keys, and the configured engine's readiness.

Doctor is what a stuck user runs, so it diagnoses and never crashes: a config
that does not load, a datasource that does not connect and a missing key are
printed, not raised. It reads the same ``--config`` as every other command.
"""
from __future__ import annotations

import sqlite3

from typer.testing import CliRunner

from nl2sql.cli.main import app
from nl2sql.testing.config import make_config

runner = CliRunner()

# A closing-tag-shaped sequence with no matching opening tag: rich raises
# MarkupError on it, so text carrying one must never reach markup parsing.
MARKUP_MARKER = "[/bold]"


def _out(result) -> str:
    return " ".join(result.output.split())


def test_doctor_names_a_missing_provider_key(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-doctor")

    result = runner.invoke(app, ["--config", str(tmp_path / "nl2sql.yaml"), "doctor"])

    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "OPENAI_API_KEY: not set" in out
    assert "ANTHROPIC_API_KEY is set" in out
    assert "sk-ant-doctor" not in out


def test_a_config_that_does_not_load_is_reported_not_raised(tmp_path):
    (tmp_path / "nl2sql.yaml").write_text(f"version: 2\ndatasources: []\n'{MARKUP_MARKER}': 1\n",
                                          encoding="utf-8")

    result = runner.invoke(app, ["--config", str(tmp_path / "nl2sql.yaml"), "doctor"])

    assert result.exit_code == 0, result.output
    out = _out(result)
    assert f"{MARKUP_MARKER} Unknown key" in out
    assert "Traceback" not in out


def test_doctor_reports_the_engine_check(tmp_path):
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        connection.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
    make_config(tmp_path, datasources=[
        {"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}},
        {"id": "lost", "type": "sqlite", "connection": {"database": "no/such/folder/lost.db"}, "required": True},
    ])

    result = runner.invoke(app, ["--config", str(tmp_path / "nl2sql.yaml"), "doctor"])

    assert result.exit_code == 0, result.output
    out = _out(result)
    assert "shop: ready" in out
    assert "lost (required): failed at database" in out
    assert "Index: missing" in out and "nl2sql index" in out
    assert "not fully ready" in out
