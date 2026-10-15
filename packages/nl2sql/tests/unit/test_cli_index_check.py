"""``nl2sql index --check``: the drift check for a cron job or a CI step (D1).

docs/architecture/wave_a.md, section 10: it prints the reports and exits 0
when nothing drifted, 1 when something did, 2 when a check failed. It never
re-indexes. The index is written here without embedding (a snapshot and a
pointer), so no model is loaded.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nl2sql import NL2SQL, load_config
from nl2sql.cli.main import app
from nl2sql.testing.config import make_config
from nl2sql_sdk.stores import BuildPointer

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _sql(path: Path, *statements: str) -> None:
    with sqlite3.connect(path) as connection:
        for statement in statements:
            connection.execute(statement)
    connection.close()


@pytest.fixture
def project(tmp_path):
    _sql(tmp_path / "books.sqlite", "CREATE TABLE Book (BookId INTEGER PRIMARY KEY, Title TEXT)")
    make_config(tmp_path, datasources=[{"id": "books", "type": "sqlite", "connection": {"database": "books.sqlite"}}])
    return tmp_path


def _index(project: Path) -> None:
    """The database's snapshot stored and the live pointer set to it, as a build would leave them."""
    with NL2SQL(load_config(project / "nl2sql.yaml")) as engine:
        ctx = engine._runtime
        adapter = ctx.ds_registry.get_adapter("books")
        version = ctx.state.put_snapshot(adapter.fetch_schema_snapshot(ctx.ds_registry.get_index_scope("books")))
        ctx.index.open(ctx.namespace, 384)
        ctx.index.set_pointers(ctx.namespace, {"books": BuildPointer(
            build_id="b1", schema_version=version, built_at=datetime.now(timezone.utc))})


def _check(project: Path, *args: str):
    result = runner.invoke(app, ["--config", str(project / "nl2sql.yaml"), "index", "--check", *args])
    return result, ANSI.sub("", result.output)


def test_nothing_drifted_exits_0(project):
    _index(project)

    result, out = _check(project)

    assert result.exit_code == 0, out
    assert "books" in out and "unchanged" in out


def test_drift_exits_1_and_prints_the_diff(project):
    _index(project)
    _sql(project / "books.sqlite", "ALTER TABLE Book ADD COLUMN Isbn TEXT", "CREATE TABLE Author (Id INTEGER)")

    result, out = _check(project, "--datasource", "books")

    assert result.exit_code == 1, out
    assert "drifted" in out and "Isbn" in out and "Author" in out


def test_a_failed_check_exits_2(project):
    _index(project)
    (project / "books.sqlite").write_bytes(b"this is not a database" * 100)

    result, out = _check(project)

    assert result.exit_code == 2, out
    assert "failed" in out


def test_json_output(project):
    _index(project)
    _sql(project / "books.sqlite", "CREATE TABLE Author (Id INTEGER)")

    result, out = _check(project, "--json")

    import json

    [report] = json.loads(result.stdout)
    assert result.exit_code == 1
    assert report["status"] == "drifted" and report["diff"]["tables_added"][0]["table_name"] == "Author"


def test_check_cannot_be_combined_with_a_build_option(project):
    result, out = _check(project, "--curated-only")

    assert result.exit_code != 0 and "--check" in out
