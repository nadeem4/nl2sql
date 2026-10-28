"""The playground's Retrieval inspector: one MMR search of the live index.

It reads index metadata (column statistics and sample values included), so it
is local only, gated exactly like Settings and Rebuild: on for a loopback bind,
refused on any other host unless ``--allow-settings``, and only from the page.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from types import SimpleNamespace

from nl2sql import NL2SQL
from nl2sql.testing.facade import StubDatasources
from nl2sql.cli.demo.playground.app import build_app
from nl2sql.embeddings import EmbeddingInfo
from nl2sql.indexing.retrieval import Retriever
from nl2sql_sdk.knowledge import DatasourceKnowledge
from nl2sql_sdk.schema import TableRef

from test_retrieval_trace import QUESTION, _store  # noqa: E402  (same directory)


def _chinook_snapshot():
    """The tables the test entries name, as bare names (the entries' ``table`` metadata is the bare name)."""
    tables = {name: SimpleNamespace(full_name=name, table=TableRef(schema_name="main", table_name=name))
              for name in ("Genre", "Track", "Invoice", "InvoiceLine", "Artist", "Employee")}
    return SimpleNamespace(contract=SimpleNamespace(tables=tables))


class _Snapshots:
    def get_snapshot(self, _ds, _version):
        return _chinook_snapshot()


class _Scope:
    """What ``policy_engine.scope`` returns: the ``analyst`` role may not read ``Employee``."""

    grants: list = []
    check = None

    def __init__(self, datasource_id, roles):
        self.datasource_id, self.roles = datasource_id, roles

    def reaches_datasource(self):
        return True

    def allows(self, _schema, table):
        return not ("analyst" in self.roles and table == "Employee")


class _Policies:
    def __init__(self):
        self.asked_as = []

    def scope(self, user_context, datasource_id):
        self.asked_as.append(user_context.roles)
        return _Scope(datasource_id, user_context.roles)


class _Registry:
    def list_ids(self):
        return ["chinook"]

    def unavailable(self):
        return {}

    def get_preferred_tables(self, _ds):
        return []


class _Engine(NL2SQL):
    """The real facade over a live index store and a stub schema store."""

    def __init__(self, store: Retriever):
        self._runtime = type("Ctx", (), {})()
        self._runtime.vector_store = store
        self._runtime.index = store.index
        self._runtime.namespace = store.namespace
        self._runtime.embedding_info = EmbeddingInfo("test", "test", 11)
        self._runtime.state = _Snapshots()
        self._runtime.ds_registry = _Registry()
        self._runtime.curated = None
        self._runtime.policy_engine = _Policies()
        self._runtime.knowledge = SimpleNamespace(get=lambda _ds: DatasourceKnowledge())
        store.state = self._runtime.state

    @property
    def datasources(self):
        return StubDatasources(getattr(self, "_runtime", None), ["chinook"])


def _client(tmp_path, host="127.0.0.1", allow_settings=False, store="filled"):
    vs = _store(tmp_path) if store == "filled" else store
    app = build_app(_Engine(vs), questions=[], roles=["admin"], mode="replay", dataset="chinook",
                    project_dir=tmp_path, host=host, allow_settings=allow_settings)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_options_say_it_is_on_and_list_the_choices(tmp_path):
    body = _client(tmp_path).get("/api/retrieval").json()

    assert body["available"] is True and body["reason"] is None
    assert body["datasource_id"] == "chinook"
    assert body["types"][:5] == ["schema.datasource", "schema.datasource_example", "schema.table", "schema.column",
                                 "schema.relationship"]
    assert body["defaults"] == {"k": 8, "lambda_mult": 0.7, "fetch_multiplier": 4}


def test_a_search_returns_the_pool_with_scores_the_picks_and_the_text(tmp_path):
    response = _client(tmp_path).post("/api/retrieval", json={"role": "admin", "query": QUESTION, "k": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == QUESTION
    assert (body["k"], body["fetch_k"], body["lambda_mult"]) == (3, 12, 0.7)
    assert len(body["pool"]) == 9 and len(body["picks"]) == 3 and len(body["dropped"]) == 6
    first = body["pool"][0]
    assert {"rank", "id", "label", "type", "similarity", "distance", "picked", "pick_order",
            "mmr_score", "redundancy", "text"} <= set(first)


def test_the_toggles_narrow_and_reweigh_the_search(tmp_path):
    client = _client(tmp_path)

    tables = client.post("/api/retrieval", json={"role": "admin", "query": QUESTION, "k": 2, "types": ["schema.table"],
                                                 "datasource_id": "chinook", "lambda_mult": 1.0}).json()
    diverse = client.post("/api/retrieval", json={"role": "admin", "query": QUESTION, "k": 2, "types": ["schema.table"],
                                                  "lambda_mult": 0.0}).json()
    other = client.post("/api/retrieval", json={"role": "admin", "query": QUESTION, "datasource_id": "sales"}).json()

    assert {e["type"] for e in tables["pool"]} == {"schema.table"}
    assert tables["filter"] == {"datasource_id": "chinook", "types": ["schema.table"]}
    # Pure relevance picks the two nearest; pure difference does not.
    assert [e["pick_order"] for e in tables["pool"][:2]] == [1, 2]
    assert diverse["pool"][1]["picked"] is False
    assert other["pool"] == []


def test_a_search_is_made_as_the_role_asked_and_shows_only_what_it_may_read(tmp_path):
    engine = _Engine(_store(tmp_path))
    app = build_app(engine, questions=[], roles=["admin", "analyst"], mode="replay", dataset="chinook",
                    project_dir=tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    tables = lambda body: {e["table"] for e in body["pool"] if e["type"] == "schema.table"}  # noqa: E731

    as_admin = client.post("/api/retrieval", json={"query": QUESTION, "role": "admin"}).json()
    as_analyst = client.post("/api/retrieval", json={"query": QUESTION, "role": "analyst"}).json()

    assert "Employee" in tables(as_admin) and "Employee" not in tables(as_analyst)
    assert engine._runtime.policy_engine.asked_as == [["admin"], ["analyst"]]
    # No default view (D33): a search that names no role is refused, as /api/schema refuses one.
    assert client.post("/api/retrieval", json={"query": QUESTION}).status_code == 422


@pytest.mark.parametrize("body", [
    {"query": ""},
    {"query": "x", "k": 0},
    {"query": "x", "lambda_mult": 1.5},
    {"query": "x", "types": ["schema.everything"]},
])
def test_bad_requests_are_refused(tmp_path, body):
    assert _client(tmp_path).post("/api/retrieval", json=body).status_code == 422


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_a_remote_bind_is_refused_without_the_flag(tmp_path, host):
    client = _client(tmp_path, host=host)

    options = client.get("/api/retrieval").json()
    response = client.post("/api/retrieval", json={"role": "admin", "query": QUESTION})

    assert options["available"] is False
    assert "--allow-settings" in options["reason"] and "sample values" in options["reason"]
    assert response.status_code == 403
    assert "pool" not in response.json()


def test_a_remote_bind_is_allowed_with_the_flag(tmp_path):
    client = _client(tmp_path, host="0.0.0.0", allow_settings=True)

    assert client.post("/api/retrieval", json={"role": "admin", "query": QUESTION}).status_code == 200


def test_a_search_must_come_from_the_page_itself(tmp_path):
    client = _client(tmp_path)

    cross = client.post("/api/retrieval", json={"role": "admin", "query": QUESTION}, headers={"Origin": "http://evil.example"})
    rebound = client.post("/api/retrieval", json={"role": "admin", "query": QUESTION}, headers={"Host": "evil.example:8765"})
    form = client.post("/api/retrieval", content=f"query={QUESTION}",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})

    assert cross.status_code == 403
    assert rebound.status_code == 403
    assert form.status_code in (415, 422)


def test_an_empty_index_answers_with_an_empty_pool(tmp_path):
    filled = _store(tmp_path)
    client = _client(tmp_path, store=Retriever(filled.index, filled.namespace, filled.embeddings, {}))

    body = client.post("/api/retrieval", json={"role": "admin", "query": QUESTION}).json()

    assert body["pool"] == [] and body["picks"] == []
