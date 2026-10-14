"""``nl2sql curated validate`` and the facade it calls, ``engine.curated.validate()``.

The checks that need no index run when the engine is built (a broken
``curated`` section is a ``ConfigError``, so the command exits 1 naming it).
The command adds the one that needs the index: every table a verified query
reads exists in the schema its datasource's live index build was made from. It prints a report and
exits non-zero on any error.
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
from nl2sql.runtime import StorePaths
from nl2sql.schema import SqliteSchemaStore
from nl2sql.storage.chroma import ChromaIndex
from nl2sql.testing.config import make_config
from nl2sql_sdk.schema import (
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableRef,
)
from nl2sql_sdk.stores import BuildPointer

# The local embedder's index namespace: the config's embeddings are the default, local.
NAMESPACE = "nl2sql__local-all-minilm-l6-v2__384"

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _query(qid: str, sql: str) -> dict:
    return {"id": qid, "description": f"{qid} description", "questions": [f"{qid}?"], "sql": sql}


GOOD = _query("book_count", "SELECT COUNT(*) AS books FROM Book")
MISSING_TABLE = _query("author_count", "SELECT COUNT(*) AS authors FROM Author")


@pytest.fixture
def project(tmp_path):
    with sqlite3.connect(tmp_path / "books.sqlite") as conn:
        conn.execute("CREATE TABLE Book (BookId INTEGER PRIMARY KEY, Title TEXT)")
    (tmp_path / "curated.yaml").write_text("datasources: {}\n", encoding="utf-8")
    make_config(tmp_path, datasources=[{"id": "books", "type": "sqlite", "connection": {"database": "books.sqlite"}}],
                curated={"file": "curated.yaml"})
    return tmp_path


def _curated(project: Path, *queries, datasource: str = "books") -> Path:
    path = project / "curated.yaml"
    path.write_text(yaml.safe_dump({"datasources": {datasource: {"verified_queries": list(queries)}}}),
                    encoding="utf-8")
    return project / "nl2sql.yaml"


def _state(project: Path) -> Path:
    return StorePaths.under(project / ".nl2sql").state


def _index(project: Path, *tables: str) -> None:
    """Stores a snapshot of ``tables`` and points the books datasource's live build at it (no embedding)."""
    _state(project).parent.mkdir(parents=True, exist_ok=True)
    store = SqliteSchemaStore(path=_state(project))
    refs = [TableRef(schema_name="main", table_name=t) for t in tables]
    version, _ = store.register_snapshot(SchemaSnapshot(
        contract=SchemaContract(datasource_id="books", engine_type="sqlite",
                                tables={r.full_name: TableContract(table=r) for r in refs}),
        metadata=SchemaMetadata(datasource_id="books", engine_type="sqlite"),
    ))
    store.close()
    index = ChromaIndex(project / ".nl2sql" / "index")
    index.open(NAMESPACE, 384)
    index.set_pointers(NAMESPACE, {"books": BuildPointer(build_id="b1", schema_version=version,
                                                         built_at=datetime.now(timezone.utc))})
    index.close()


def _invoke(config: Path):
    result = runner.invoke(app, ["--config", str(config), "curated", "validate"])
    return result, ANSI.sub("", result.output)


def test_a_good_file_passes_and_exits_zero(project):
    _index(project, "Book")

    result, out = _invoke(_curated(project, GOOD))

    assert result.exit_code == 0, out
    assert "book_count" in out
    assert "OK" in out


def test_a_table_missing_from_the_indexed_schema_fails(project):
    _index(project, "Book")

    result, out = _invoke(_curated(project, GOOD, MISSING_TABLE))

    assert result.exit_code == 1, out
    assert "author_count" in out
    assert "Author" in out


def test_table_names_match_case_insensitively(project):
    _index(project, "Book")

    result, out = _invoke(_curated(project, _query("lower", "SELECT COUNT(*) FROM book")))

    assert result.exit_code == 0, out


def test_without_an_index_the_table_check_is_skipped_and_said_so(project):
    result, out = _invoke(_curated(project, GOOD, MISSING_TABLE))

    assert result.exit_code == 0, out
    assert "not indexed" in out


def test_a_file_that_breaks_the_models_exits_one(project):
    result, out = _invoke(_curated(project, GOOD, GOOD))

    assert result.exit_code == 1, out
    assert "book_count" in out


def test_unsafe_sql_exits_one_and_names_the_query(project):
    result, out = _invoke(_curated(project, GOOD, _query("wipe", "DELETE FROM Book")))

    assert result.exit_code == 1, out
    assert "wipe" in out
    assert "read-only" in out


def test_an_unknown_datasource_exits_one(project):
    result, out = _invoke(_curated(project, GOOD, datasource="bookz"))

    assert result.exit_code == 1, out
    assert "bookz" in out


def test_a_missing_file_exits_one(project):
    (project / "curated.yaml").unlink()

    result, out = _invoke(project / "nl2sql.yaml")

    assert result.exit_code == 1, out
    assert "curated.yaml" in out


def test_the_facade_returns_the_report(project):
    _index(project, "Book")

    with NL2SQL(load_config(_curated(project, GOOD, MISSING_TABLE))) as engine:
        report = engine.curated.validate()

    assert not report.ok
    by_id = {e.query_id: e for e in report.entries}
    assert by_id["book_count"].status == "OK"
    assert by_id["author_count"].status == "ERROR"
    assert "Author" in by_id["author_count"].details
