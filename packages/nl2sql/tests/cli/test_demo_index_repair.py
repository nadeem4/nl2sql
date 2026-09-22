"""`nl2sql demo` repairs a broken index at startup and flags old demo folders.

The incident: a demo folder had ``data/vector_store_demo`` on disk and 0
entries in it. ``prepare_project`` looked only at whether the folder existed,
so every later start skipped indexing and every question failed.
"""
from __future__ import annotations

import json
import re

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.cli.commands import demo as demo_cmd
from nl2sql.cli.demo import stamp
from nl2sql.indexing.vector_store import VectorStore
from nl2sql.schema.sqlite_store import SqliteSchemaStore

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return " ".join(_ANSI.sub("", text).split())


@pytest.fixture
def calls(monkeypatch):
    """Records index_demo_data calls instead of loading an embedding model."""
    seen = {"count": 0, "result": True}

    def _index(self, enrich=False):
        seen["count"] += 1
        return seen["result"]

    monkeypatch.setattr("nl2sql.cli.demo.manager.DemoManager.index_demo_data", _index)
    return seen


@pytest.fixture
def project(tmp_path, calls, monkeypatch):
    """A scaffolded demo folder whose first indexing was stubbed out."""
    directory = tmp_path / "demo"
    monkeypatch.chdir(tmp_path)
    demo_cmd.prepare_project(directory)
    calls["count"] = 0
    return directory


def _index(directory, version=None):
    store = VectorStore("nl2sql_store", str(directory / "data" / "vector_store_demo"),
                        embeddings=FakeEmbeddings(size=8))
    if version:
        store.vectorstore.add_documents([
            Document(page_content="Datasource: chinook",
                     metadata={"type": "schema.datasource", "datasource_id": "chinook", "schema_version": version}),
        ])
    return store


def _snapshot(directory, version):
    store = SqliteSchemaStore(directory / "data" / "schema_store.db")
    store._connection.execute(
        "INSERT INTO schema_snapshots VALUES ('chinook', ?, 'f', '{}', '{}', 1)", (version,)
    )
    store._connection.commit()
    store.close()


def test_a_new_folder_is_indexed(tmp_path, calls, monkeypatch):
    monkeypatch.chdir(tmp_path)
    demo_cmd.prepare_project(tmp_path / "fresh")

    assert calls["count"] == 1


def test_an_empty_index_is_repaired_at_demo_startup(project, calls, capsys):
    _index(project)  # the folder and the collection exist; nothing is in it
    assert (project / "data" / "vector_store_demo").is_dir()

    demo_cmd.prepare_project(project)

    assert calls["count"] == 1
    assert "empty" in _plain(capsys.readouterr().out).lower()


def test_an_index_behind_the_latest_snapshot_is_rebuilt(project, calls):
    _index(project, version="v1")
    _snapshot(project, "v2")

    demo_cmd.prepare_project(project)

    assert calls["count"] == 1


def test_a_healthy_index_is_left_alone(project, calls):
    _index(project, version="v1")
    _snapshot(project, "v1")

    demo_cmd.prepare_project(project)

    assert calls["count"] == 0


def test_a_failed_repair_is_reported_not_ignored(project, calls, capsys):
    _index(project)
    calls["result"] = False

    demo_cmd.prepare_project(project)

    out = _plain(capsys.readouterr().out)
    assert "Indexing failed" in out
    assert "Rebuild" in out


def test_a_new_demo_folder_is_stamped_with_the_engine_version(project):
    body = json.loads((project / stamp.STAMP_FILE).read_text(encoding="utf-8"))

    assert body["engine_version"] == stamp.engine_version()


def test_a_folder_without_a_stamp_is_flagged_as_older(project, capsys):
    (project / stamp.STAMP_FILE).unlink()

    demo_cmd.prepare_project(project)

    out = _plain(capsys.readouterr().out)
    assert "older version" in out
    assert "--dir" in out


def test_a_folder_stamped_by_an_older_engine_is_flagged(project, capsys):
    (project / stamp.STAMP_FILE).write_text(json.dumps({"engine_version": "0.0.1"}), encoding="utf-8")

    demo_cmd.prepare_project(project)

    out = _plain(capsys.readouterr().out)
    assert "0.0.1" in out and "--dir" in out


def test_a_folder_stamped_by_this_engine_is_not_flagged(project, capsys):
    demo_cmd.prepare_project(project)

    assert "older version" not in _plain(capsys.readouterr().out)


def test_index_demo_data_reports_a_failed_run_instead_of_exiting(tmp_path, monkeypatch):
    """`run_indexing` ends with sys.exit(1) on failure; the demo must survive it."""
    from nl2sql.cli.demo.manager import DemoManager
    from nl2sql.cli.console import console

    monkeypatch.chdir(tmp_path)
    manager = DemoManager(console, tmp_path)
    manager.setup_chinook()

    def _failing(ctx, enrich=True):
        raise SystemExit(1)

    monkeypatch.setattr("nl2sql.cli.common.indexing.run_indexing", _failing)
    monkeypatch.setattr("nl2sql.context.NL2SQLContext", lambda **kwargs: object())

    assert manager.index_demo_data() is False
