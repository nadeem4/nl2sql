"""``nl2sql knowledge show``, ``export`` and ``sources`` (docs/architecture/wave_b.md, section 3, K1).

The index is written here without embedding (a snapshot and a pointer), so no
model is loaded.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nl2sql import NL2SQL, load_config
from nl2sql.cli.main import app
from nl2sql.testing.config import make_config
from nl2sql_sdk.stores import BuildPointer

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "reader": {"description": "Books only", "allow": ["books.main.Book"]}}


@pytest.fixture
def project(tmp_path):
    with sqlite3.connect(tmp_path / "books.sqlite") as connection:
        connection.execute("CREATE TABLE Book (BookId INTEGER PRIMARY KEY, Title TEXT)")
        connection.execute("CREATE TABLE Royalty (BookId INTEGER, Amount REAL)")
    connection.close()
    (tmp_path / "glossary.yaml").write_text(yaml.safe_dump({"version": 1, "datasources": {"books": {
        "tables": {"Book": {"description": "One row per title."}, "Royalty": {"description": "Pay per book."}},
        "terms": [{"term": "catalog", "definition": "Every Book."},
                  {"term": "earnings", "definition": "Sum of Royalty.Amount."}]}}}), encoding="utf-8")
    make_config(tmp_path, datasources=[{"id": "books", "type": "sqlite", "connection": {"database": "books.sqlite"}}],
                policies={"roles": ROLES}, knowledge=[{"type": "yaml", "path": "glossary.yaml"}])
    with NL2SQL(load_config(tmp_path / "nl2sql.yaml")) as engine:
        ctx = engine._runtime
        adapter = ctx.ds_registry.get_adapter("books")
        version = ctx.state.put_snapshot(adapter.fetch_schema_snapshot(ctx.ds_registry.get_index_scope("books")))
        ctx.index.open(ctx.namespace, 384)
        ctx.index.set_pointers(ctx.namespace, {"books": BuildPointer(
            build_id="b1", schema_version=version, built_at=datetime.now(timezone.utc))})
    return tmp_path


def _run(project: Path, *args: str):
    """The result and its stdout: the event log goes to stderr, so a piped export stays a clean file."""
    result = runner.invoke(app, ["--config", str(project / "nl2sql.yaml"), "knowledge", *args])
    return result, ANSI.sub("", result.stdout)


def test_show_lists_items_with_their_origin(project):
    result, out = _run(project, "show", "--as", "admin")

    assert result.exit_code == 0, out
    assert "One row per title." in out and "(knowledge: yaml)" in out
    assert "identifier" in out and "(derived)" in out


def test_show_hides_what_the_role_cannot_read(project):
    result, out = _run(project, "show", "--as", "reader")

    assert result.exit_code == 0, out
    assert "catalog" in out and "Royalty" not in out and "earnings" not in out


def test_show_exits_1_with_problems_last(project):
    (project / "glossary.yaml").write_text(yaml.safe_dump({"version": 1, "datasources": {"books": {
        "tables": {"Bok": {"description": "A typo."}}}}}), encoding="utf-8")
    result, out = _run(project, "show", "--as", "admin")

    assert result.exit_code == 1
    assert out.rstrip().splitlines()[-1].strip().startswith("books:") and "Bok" in out.split("Problems")[1]


def test_a_caller_is_required(project):
    result, out = _run(project, "show")
    assert result.exit_code == 1 and "--as" in out
    result, out = _run(project, "show", "--as", "nobody")
    assert result.exit_code == 1 and "Unknown role" in out


def test_export_prints_or_writes_the_glossary(project):
    result, out = _run(project, "export", "--as", "reader")
    assert result.exit_code == 0, out
    assert yaml.safe_load(out)["datasources"]["books"]["terms"] == [{"term": "catalog", "definition": "Every Book."}]

    result, out = _run(project, "export", "--as", "admin", "--out", str(project / "out.yaml"))
    assert result.exit_code == 0, out
    assert "earnings" in (project / "out.yaml").read_text(encoding="utf-8")

    result, out = _run(project, "export", "--as", "admin", "--out", str(project / "glossary.yaml"))
    assert result.exit_code == 1 and "overwrite" in out


def test_sources_lists_each_source(project):
    result, out = _run(project, "sources", "--as", "reader")

    assert result.exit_code == 0, out
    assert out.startswith("yaml") and "books: 1 tables, 1 terms" in out
