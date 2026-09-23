"""``nl2sql doctor`` checks the vector index by its contents.

An empty index fails every question at the resolver while every other check
is green, so doctor reports entry counts by type and whether the index was
built from the latest schema snapshot.
"""
from __future__ import annotations

import re

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import FakeEmbeddings
from rich.console import Console
from typer.testing import CliRunner

from nl2sql.cli.demo.manager import DemoManager
from nl2sql.cli.main import app
from nl2sql.indexing.vector_store import VectorStore
from nl2sql.schema.sqlite_store import SqliteSchemaStore

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return " ".join(_ANSI.sub("", text).split())


@pytest.fixture()
def demo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "200")
    DemoManager(Console(quiet=True), tmp_path).setup_demo()
    return tmp_path


def _index(root, version=None):
    store = VectorStore("nl2sql_store", str(root / "data" / "vector_store_demo"), embeddings=FakeEmbeddings(size=8))
    if version:
        store.vectorstore.add_documents([
            Document(page_content="d", metadata={"type": "schema.datasource", "datasource_id": "chinook", "schema_version": version}),
            Document(page_content="t", metadata={"type": "schema.table", "datasource_id": "chinook", "schema_version": version}),
        ])


def _snapshot(root, version):
    store = SqliteSchemaStore(root / "data" / "schema_store.db")
    store._connection.execute("INSERT INTO schema_snapshots VALUES ('chinook', ?, 'f', '{}', '{}', 1)", (version,))
    store._connection.commit()
    store.close()


def test_doctor_reports_an_empty_index(demo):
    _index(demo)

    result = runner.invoke(app, ["--env", "demo", "doctor"])

    out = _plain(result.output)
    assert result.exit_code == 0, result.output
    assert "Index" in out
    assert "vector index is empty" in out
    assert "nl2sql index" in out


def test_doctor_reports_counts_and_a_matching_schema_version(demo):
    _index(demo, version="v7")
    _snapshot(demo, "v7")

    result = runner.invoke(app, ["--env", "demo", "doctor"])

    out = _plain(result.output)
    assert "schema.datasource=1" in out and "schema.table=1" in out
    assert "v7" in out
    assert "matches the latest snapshot" in out


def test_doctor_reports_a_stale_index(demo):
    _index(demo, version="v1")
    _snapshot(demo, "v2")

    out = _plain(runner.invoke(app, ["--env", "demo", "doctor"]).output)

    assert "latest snapshot is v2" in out
