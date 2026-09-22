"""The playground's Retrieval inspector: one MMR search of the live index.

It reads index metadata (column statistics and sample values included), so it
is local only, gated exactly like Settings and Rebuild: on for a loopback bind,
refused on any other host unless ``--allow-settings``, and only from the page.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from nl2sql import NL2SQL
from nl2sql.cli.demo.playground.app import build_app
from nl2sql.indexing.vector_store import VectorStore

from test_retrieval_trace import QUESTION, _store  # noqa: E402  (same directory)


class _Snapshots:
    def get_latest_version(self, _ds):
        return "v1"

    def get_latest_snapshot(self, _ds):
        return None


class _Engine(NL2SQL):
    """The real facade over a live vector store and a stub schema store."""

    def __init__(self, store):
        self._ctx = type("Ctx", (), {})()
        self._ctx.vector_store = store
        self._ctx.schema_store = _Snapshots()
        self._ctx.ds_registry = type("R", (), {"list_ids": staticmethod(lambda: ["chinook"])})()

    def list_datasources(self):
        return ["chinook"]


def _client(tmp_path, host="127.0.0.1", allow_settings=False, store="filled"):
    vs = _store(tmp_path) if store == "filled" else store
    app = build_app(_Engine(vs), questions=[], roles=["admin"], mode="replay", dataset="chinook",
                    project_dir=tmp_path, host=host, allow_settings=allow_settings)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def test_options_say_it_is_on_and_list_the_choices(tmp_path):
    body = _client(tmp_path).get("/api/retrieval").json()

    assert body["available"] is True and body["reason"] is None
    assert body["datasource_id"] == "chinook"
    assert body["types"][:4] == ["schema.datasource", "schema.table", "schema.column", "schema.relationship"]
    assert body["defaults"] == {"k": 8, "lambda_mult": 0.7, "fetch_multiplier": 4}


def test_a_search_returns_the_pool_with_scores_the_picks_and_the_text(tmp_path):
    response = _client(tmp_path).post("/api/retrieval", json={"query": QUESTION, "k": 3})

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

    tables = client.post("/api/retrieval", json={"query": QUESTION, "k": 2, "types": ["schema.table"],
                                                 "datasource_id": "chinook", "lambda_mult": 1.0}).json()
    diverse = client.post("/api/retrieval", json={"query": QUESTION, "k": 2, "types": ["schema.table"],
                                                  "lambda_mult": 0.0}).json()
    other = client.post("/api/retrieval", json={"query": QUESTION, "datasource_id": "sales"}).json()

    assert {e["type"] for e in tables["pool"]} == {"schema.table"}
    assert tables["filter"] == {"datasource_id": "chinook", "types": ["schema.table"]}
    # Pure relevance picks the two nearest; pure difference does not.
    assert [e["pick_order"] for e in tables["pool"][:2]] == [1, 2]
    assert diverse["pool"][1]["picked"] is False
    assert other["pool"] == []


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
    response = client.post("/api/retrieval", json={"query": QUESTION})

    assert options["available"] is False
    assert "--allow-settings" in options["reason"] and "sample values" in options["reason"]
    assert response.status_code == 403
    assert "pool" not in response.json()


def test_a_remote_bind_is_allowed_with_the_flag(tmp_path):
    client = _client(tmp_path, host="0.0.0.0", allow_settings=True)

    assert client.post("/api/retrieval", json={"query": QUESTION}).status_code == 200


def test_a_search_must_come_from_the_page_itself(tmp_path):
    client = _client(tmp_path)

    cross = client.post("/api/retrieval", json={"query": QUESTION}, headers={"Origin": "http://evil.example"})
    rebound = client.post("/api/retrieval", json={"query": QUESTION}, headers={"Host": "evil.example:8765"})
    form = client.post("/api/retrieval", content=f"query={QUESTION}",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})

    assert cross.status_code == 403
    assert rebound.status_code == 403
    assert form.status_code in (415, 422)


def test_no_vector_index_is_a_plain_error(tmp_path):
    client = _client(tmp_path, store=None)

    response = client.post("/api/retrieval", json={"query": QUESTION})

    assert response.status_code == 409
    assert "no vector index" in response.json()["detail"]


def test_an_empty_index_answers_with_an_empty_pool(tmp_path):
    client = _client(tmp_path, store=VectorStore("nl2sql_store", str(tmp_path / "empty"),
                                                 embeddings=_store(tmp_path / "x").embeddings))

    body = client.post("/api/retrieval", json={"query": QUESTION}).json()

    assert body["pool"] == [] and body["picks"] == []
