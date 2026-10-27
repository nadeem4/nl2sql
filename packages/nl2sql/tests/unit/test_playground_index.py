"""The playground's index panel: health on load, and a guarded Rebuild.

The owner's rule: the playground always offers an index action. Rebuild uses
the settings panel's guard (local only unless ``--allow-settings``) and offers
no model-written descriptions: a draft is a proposal, not part of the index
(wave_b.md, B31). A rebuild does not wait for questions in flight: each run pinned the index pointers when it started, and
the build a switch replaces is kept for it.
"""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from index_helpers import Chunk, TestIndex  # same directory
from nl2sql.storage.sqlite import SqliteState
from langchain_core.embeddings import FakeEmbeddings

from nl2sql import NL2SQL
from nl2sql.testing.facade import StubDatasources
from nl2sql.cli.demo.playground.app import build_app
from nl2sql.indexing import rebuild as rebuild_module
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql_sdk.schema import SchemaContract, SchemaMetadata, SchemaSnapshot


class _Adapter:
    datasource_id = "chinook"


class _Snapshots(SqliteState):
    """A state store on ``:memory:`` whose every snapshot is an empty one."""

    def __init__(self):
        super().__init__(":memory:")
        self.migrate()

    def get_snapshot(self, ds, _version):
        return SchemaSnapshot(contract=SchemaContract(datasource_id=ds, engine_type="stub", tables={}),
                              metadata=SchemaMetadata(datasource_id=ds, engine_type="stub", tables={}))


class _Registry:
    def list_ids(self):
        return ["chinook"]

    def list_adapters(self):
        return [_Adapter()]

    def unavailable(self):
        return {}

    def get_preferred_tables(self, _ds):
        return []


class _Context:
    """The parts of the runtime the index panel reaches, over a real index store in ``tmp_path``."""

    def __init__(self, tmp_path):
        self.test_index = TestIndex(tmp_path, FakeEmbeddings(size=8), state=_Snapshots())
        for key, value in vars(self.test_index.ctx).items():
            setattr(self, key, value)
        self.ds_registry = _Registry()
        self.curated = None

    @property
    def vector_store(self):
        return self.test_index.retriever()


def _chunk(kind, text):
    return Chunk(text, type=kind, datasource_id="chinook", schema_version="v1", table=text)


class _Engine(NL2SQL):
    """The real facade over a live vector store in ``tmp_path``."""

    def __init__(self, tmp_path):
        self._runtime = _Context(tmp_path)

    @property
    def datasources(self):
        return StubDatasources(getattr(self, "_runtime", None), ["chinook"])


@pytest.fixture
def scripted(monkeypatch):
    """An orchestrator that writes a full chinook entry set at schema v1."""

    class _Orchestrator:
        built = []

        def __init__(self, ctx):
            _Orchestrator.built.append(ctx)
            self.ctx = ctx

        def index_datasource(self, adapter):
            IndexingOrchestrator(self.ctx)._commit(
                "chinook", "v1",
                [_chunk("schema.datasource", "Datasource: chinook"), _chunk("schema.table", "Table: Track")],
                [], None,
            )
            return {"datasource_id": "chinook", "schema_version": "v1", "schema.datasource": 1, "schema.table": 1}

    monkeypatch.setattr(rebuild_module, "IndexingOrchestrator", _Orchestrator)
    return _Orchestrator


def _client(tmp_path, host="127.0.0.1", allow_settings=False, mode="replay", engine=None):
    engine = engine or _Engine(tmp_path)
    app = build_app(engine, questions=[], roles=["admin"], mode=mode, dataset="chinook",
                    project_dir=tmp_path, host=host, allow_settings=allow_settings)
    return engine, TestClient(app, base_url="http://127.0.0.1:8765")


def _wait_for(client, state, timeout=10.0):
    deadline = time.time() + timeout
    body = client.get("/api/index").json()
    while body["job"]["state"] != state and time.time() < deadline:
        time.sleep(0.05)
        body = client.get("/api/index").json()
    return body


def test_health_is_shown_on_load_and_a_missing_index_offers_rebuild(tmp_path):
    _, client = _client(tmp_path)

    body = client.get("/api/index").json()

    assert body["health"]["status"] == "missing"
    assert body["rebuild"]["available"] is True
    assert body["job"]["state"] == "idle"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_rebuild_is_refused_on_a_non_loopback_host_without_the_flag(tmp_path, scripted, host):
    engine, client = _client(tmp_path, host=host)

    read = client.get("/api/index").json()
    assert read["rebuild"]["available"] is False
    assert "--allow-settings" in read["rebuild"]["reason"]

    response = client.post("/api/index/rebuild", json={})

    assert response.status_code == 403
    assert scripted.built == []
    assert engine._runtime.vector_store.is_empty()


def test_rebuild_is_allowed_on_a_non_loopback_host_with_the_flag(tmp_path, scripted):
    _, client = _client(tmp_path, host="0.0.0.0", allow_settings=True)

    assert client.post("/api/index/rebuild", json={}).status_code == 202
    assert _wait_for(client, "done")["health"]["status"] == "ok"


def test_rebuild_must_come_from_the_page_itself(tmp_path, scripted):
    _, client = _client(tmp_path)

    response = client.post("/api/index/rebuild", json={},
                           headers={"Origin": "http://evil.example"})

    assert response.status_code == 403


def test_rebuild_replaces_an_empty_index_and_reports_progress(tmp_path, scripted):
    _, client = _client(tmp_path)

    assert client.post("/api/index/rebuild", json={}).status_code == 202
    body = _wait_for(client, "done")

    assert body["health"]["status"] == "ok"
    assert body["health"]["counts"] == {"schema.datasource": 1, "schema.table": 1}
    assert body["health"]["built_at"]
    assert any(step.startswith("Loading the embedding model") for step in body["job"]["steps"])
    assert len(scripted.built) == 1


def test_the_rebuild_offers_no_model_written_descriptions(tmp_path, scripted):
    _, client = _client(tmp_path, mode="live")

    assert "enrich_available" not in client.get("/api/index").json()["rebuild"]
    assert client.post("/api/index/rebuild", json={"enrich": True}).status_code == 202
    _wait_for(client, "done")


def test_a_second_rebuild_while_one_runs_is_refused(tmp_path, monkeypatch):
    release = threading.Event()

    def _slow(ctx, on_progress=None, **_kw):
        release.wait(5)
        return rebuild_module.RebuildResult(ok=True)

    monkeypatch.setattr(rebuild_module, "rebuild_index", _slow)
    _, client = _client(tmp_path)

    assert client.post("/api/index/rebuild", json={}).status_code == 202
    assert client.post("/api/index/rebuild", json={}).status_code == 409
    release.set()
    _wait_for(client, "done")


def test_a_rebuild_does_not_wait_for_questions_in_flight(tmp_path, scripted):
    engine = _Engine(tmp_path)
    app = build_app(engine, questions=[], roles=["admin"], mode="replay", dataset="chinook",
                    project_dir=tmp_path, host="127.0.0.1")
    client = TestClient(app, base_url="http://127.0.0.1:8765")

    with app.state.settings_panel.gate.run():  # a question in flight
        assert client.post("/api/index/rebuild", json={}).status_code == 202
        assert _wait_for(client, "done")["health"]["status"] == "ok"


def test_a_failed_rebuild_is_reported_and_keeps_the_previous_index(tmp_path, monkeypatch):
    class _Failing:
        def __init__(self, ctx):
            pass

        def index_datasource(self, adapter):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(rebuild_module, "IndexingOrchestrator", _Failing)
    engine, client = _client(tmp_path)
    engine._runtime.test_index.write("chinook", [_chunk("schema.datasource", "old")])

    client.post("/api/index/rebuild", json={})
    body = _wait_for(client, "failed")

    assert "database is locked" in body["job"]["error"]
    assert "previous index" in body["job"]["error"]
    assert body["health"]["total"] == 1


def test_an_old_demo_folder_is_flagged(tmp_path):
    _, client = _client(tmp_path)  # tmp_path has no stamp

    folder = client.get("/api/index").json()["folder"]

    assert folder["warning"] and "--dir" in folder["warning"]
