"""``nl2sql curated validate`` and the facade it calls, ``CuratedAPI.validate()``.

The command runs the startup checks plus the one that needs the index: every
table a verified query reads exists in its datasource's latest indexed schema.
It prints a report and exits non-zero on any error.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from nl2sql import CuratedAPI
from nl2sql.cli.main import app
from nl2sql.common.settings import settings
from nl2sql.schema import SqliteSchemaStore
from nl2sql_adapter_sdk.schema import (
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableRef,
)

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _query(qid: str, sql: str) -> dict:
    return {"id": qid, "description": f"{qid} description", "questions": [f"{qid}?"], "sql": sql}


GOOD = _query("book_count", "SELECT COUNT(*) AS books FROM Book")
MISSING_TABLE = _query("author_count", "SELECT COUNT(*) AS authors FROM Author")


@pytest.fixture
def project(tmp_path, monkeypatch):
    database = tmp_path / "books.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE Book (BookId INTEGER PRIMARY KEY, Title TEXT)")
    (tmp_path / "datasources.yaml").write_text(yaml.safe_dump({"version": 1, "datasources": [{
        "id": "books", "connection": {"type": "sqlite", "database": str(database)},
    }]}), encoding="utf-8")
    (tmp_path / "secrets.yaml").write_text("version: 1\nproviders: []\n", encoding="utf-8")
    monkeypatch.setattr(settings, "schema_store_backend", "sqlite")
    monkeypatch.setattr(settings, "schema_store_path", str(tmp_path / "data" / "schema_store.db"))
    return tmp_path


def _curated(project: Path, *queries, datasource: str = "books") -> Path:
    path = project / "curated.yaml"
    path.write_text(yaml.safe_dump({"datasources": {datasource: {"verified_queries": list(queries)}}}),
                    encoding="utf-8")
    return path


def _index(project: Path, *tables: str) -> None:
    store = SqliteSchemaStore(path=Path(settings.schema_store_path))
    refs = [TableRef(schema_name="main", table_name=t) for t in tables]
    store.register_snapshot(SchemaSnapshot(
        contract=SchemaContract(datasource_id="books", engine_type="sqlite",
                                tables={r.full_name: TableContract(table=r) for r in refs}),
        metadata=SchemaMetadata(datasource_id="books", engine_type="sqlite"),
    ))
    store.close()


def _invoke(project: Path, curated: Path):
    result = runner.invoke(app, ["curated", "validate", "--curated", str(curated),
                                 "--config", str(project / "datasources.yaml"),
                                 "--secrets", str(project / "secrets.yaml")])
    return result, ANSI.sub("", result.output)


def test_a_good_file_passes_and_exits_zero(project):
    _index(project, "Book")

    result, out = _invoke(project, _curated(project, GOOD))

    assert result.exit_code == 0, out
    assert "book_count" in out
    assert "OK" in out


def test_a_table_missing_from_the_indexed_schema_fails(project):
    _index(project, "Book")

    result, out = _invoke(project, _curated(project, GOOD, MISSING_TABLE))

    assert result.exit_code == 1, out
    assert "author_count" in out
    assert "Author" in out


def test_table_names_match_case_insensitively(project):
    _index(project, "Book")

    result, out = _invoke(project, _curated(project, _query("lower", "SELECT COUNT(*) FROM book")))

    assert result.exit_code == 0, out


def test_without_an_index_the_table_check_is_skipped_and_said_so(project):
    result, out = _invoke(project, _curated(project, GOOD, MISSING_TABLE))

    assert result.exit_code == 0, out
    assert "not indexed" in out
    # Checking must not create a schema store.
    assert not Path(settings.schema_store_path).exists()


def test_a_file_that_breaks_the_models_exits_one(project):
    result, out = _invoke(project, _curated(project, GOOD, GOOD))

    assert result.exit_code == 1, out
    assert "book_count" in out


def test_unsafe_sql_exits_one_and_names_the_query(project):
    result, out = _invoke(project, _curated(project, GOOD, _query("wipe", "DELETE FROM Book")))

    assert result.exit_code == 1, out
    assert "wipe" in out
    assert "read-only" in out


def test_an_unknown_datasource_exits_one(project):
    result, out = _invoke(project, _curated(project, GOOD, datasource="bookz"))

    assert result.exit_code == 1, out
    assert "bookz" in out


def test_a_missing_file_exits_one(project):
    result, out = _invoke(project, project / "nope.yaml")

    assert result.exit_code == 1, out
    assert "nope.yaml" in out


def test_the_facade_returns_the_report(project):
    _index(project, "Book")

    report = CuratedAPI().validate(
        curated_path=_curated(project, GOOD, MISSING_TABLE),
        datasources_path=project / "datasources.yaml",
        secrets_path=project / "secrets.yaml",
    )

    assert not report.ok
    by_id = {e.query_id: e for e in report.entries}
    assert by_id["book_count"].status == "OK"
    assert by_id["author_count"].status == "ERROR"
    assert "Author" in by_id["author_count"].details
