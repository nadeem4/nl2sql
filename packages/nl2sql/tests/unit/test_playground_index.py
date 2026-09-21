"""The playground's index panel: health on load, and a guarded Rebuild.

The owner's rule: the playground always offers an index action. Rebuild uses
the settings panel's guardrails (local only unless ``--allow-settings``, the
same ``RunGate`` so questions in flight finish before the switch), and LLM
enrichment is an opt-in because it spends tokens.
"""
from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.cli.demo.playground import index_panel
from nl2sql.cli.demo.playground.app import build_app
from nl2sql.indexing import rebuild as rebuild_module
from nl2sql.indexing.vector_store import VectorStore


class _Adapter:
    datasource_id = "chinook"


class _Snapshots:
    def __init__(self):
        self.version = "v1"

    def get_latest_version(self, _ds):
        return self.version

    def get_latest_snapshot(self, _ds):
        return None


class _Context:
    def __init__(self, tmp_path):
        self.vector_store = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=FakeEmbeddings(size=8))
        self.schema_store = _Snapshots()
        self.ds_registry = type("R", (), {"list_adapters": staticmethod(lambda: [_Adapter()])})()
        self.llm_registry = None


class _Chunk:
    def __init__(self, kind, text):
        self.type = kind
        self._text = text

    def get_page_content(self):
        return self._text

    def get_metadata(self):
        return {"type": self.type, "datasource_id": "chinook", "schema_version": "v1", "table": self._text}


class _Engine:
    def __init__(self, tmp_path):
        self.context = _Context(tmp_path)


@pytest.fixture
def scripted(monkeypatch):
    """An orchestrator that writes a full chinook entry set at schema v1."""

    class _Orchestrator:
        enrich_seen = []

        def __init__(self, ctx, enrich=True):
            _Orchestrator.enrich_seen.append(enrich)

        def index_datasource(self, adapter, vector_store=None, switch_guard=None):
            return vector_store.refresh_schema_chunks(
                "chinook", "v1",
                [_Chunk("schema.datasource", "Datasource: chinook"), _Chunk("schema.table", "Table: Track")],
                [], switch_guard=switch_guard,
            )

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


def test_health_is_shown_on_load_and_an_empty_index_offers_rebuild(tmp_path):
    _, client = _client(tmp_path)

    body = client.get("/api/index").json()

    assert body["health"]["status"] == "empty"
    assert body["rebuild"]["available"] is True
    assert body["job"]["state"] == "idle"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_rebuild_is_refused_on_a_non_loopback_host_without_the_flag(tmp_path, scripted, host):
    engine, client = _client(tmp_path, host=host)

    read = client.get("/api/index").json()
    assert read["rebuild"]["available"] is False
    assert "--allow-settings" in read["rebuild"]["reason"]

    response = client.post("/api/index/rebuild", json={"enrich": False})

    assert response.status_code == 403
    assert scripted.enrich_seen == []
    assert engine.context.vector_store.is_empty()


def test_rebuild_is_allowed_on_a_non_loopback_host_with_the_flag(tmp_path, scripted):
    _, client = _client(tmp_path, host="0.0.0.0", allow_settings=True)

    assert client.post("/api/index/rebuild", json={"enrich": False}).status_code == 202
    assert _wait_for(client, "done")["health"]["status"] == "ok"


def test_rebuild_must_come_from_the_page_itself(tmp_path, scripted):
    _, client = _client(tmp_path)

    response = client.post("/api/index/rebuild", json={"enrich": False},
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
    # Enrichment is off unless asked for.
    assert scripted.enrich_seen == [False]


def test_enrichment_is_refused_without_a_key(tmp_path, scripted):
    _, client = _client(tmp_path, mode="replay")

    assert client.get("/api/index").json()["rebuild"]["enrich_available"] is False
    response = client.post("/api/index/rebuild", json={"enrich": True})

    assert response.status_code == 400
    assert scripted.enrich_seen == []


def test_enrichment_is_passed_through_in_live_mode(tmp_path, scripted):
    _, client = _client(tmp_path, mode="live")

    assert client.post("/api/index/rebuild", json={"enrich": True}).status_code == 202
    _wait_for(client, "done")

    assert scripted.enrich_seen == [True]


def test_a_second_rebuild_while_one_runs_is_refused(tmp_path, monkeypatch):
    release = threading.Event()

    def _slow(ctx, enrich=False, on_progress=None, switch_guard=None, **_kw):
        release.wait(5)
        return rebuild_module.RebuildResult(ok=True)

    monkeypatch.setattr(index_panel, "rebuild_index", _slow)
    _, client = _client(tmp_path)

    assert client.post("/api/index/rebuild", json={}).status_code == 202
    assert client.post("/api/index/rebuild", json={}).status_code == 409
    release.set()
    _wait_for(client, "done")


def test_the_switch_waits_for_questions_in_flight(tmp_path, monkeypatch):
    events = []

    def _fake(ctx, enrich=False, on_progress=None, switch_guard=None, **_kw):
        with switch_guard():
            events.append("switched")
        return rebuild_module.RebuildResult(ok=True)

    monkeypatch.setattr(index_panel, "rebuild_index", _fake)
    engine = _Engine(tmp_path)
    app = build_app(engine, questions=[], roles=["admin"], mode="replay", dataset="chinook",
                    project_dir=tmp_path, host="127.0.0.1")
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    gate = app.state.settings_panel.gate

    with gate.run():  # a question in flight
        assert client.post("/api/index/rebuild", json={}).status_code == 202
        time.sleep(0.3)
        assert events == []
    assert _wait_for(client, "done")["job"]["state"] == "done"
    assert events == ["switched"]


def test_a_failed_rebuild_is_reported_and_keeps_the_previous_index(tmp_path, monkeypatch):
    class _Failing:
        def __init__(self, ctx, enrich=True):
            pass

        def index_datasource(self, adapter, vector_store=None, switch_guard=None):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(rebuild_module, "IndexingOrchestrator", _Failing)
    engine, client = _client(tmp_path)
    engine.context.vector_store.vectorstore.add_documents([Document(page_content="old", metadata={
        "type": "schema.datasource", "datasource_id": "chinook", "schema_version": "v1"})])

    client.post("/api/index/rebuild", json={})
    body = _wait_for(client, "failed")

    assert "database is locked" in body["job"]["error"]
    assert "previous index" in body["job"]["error"]
    assert body["health"]["total"] == 1


def test_an_old_demo_folder_is_flagged(tmp_path):
    _, client = _client(tmp_path)  # tmp_path has no stamp

    folder = client.get("/api/index").json()["folder"]

    assert folder["warning"] and "--dir" in folder["warning"]
