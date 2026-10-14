"""Answer feedback in the playground: rate a run, list the ratings.

The server records what it answered, not what the page sends: the page names a
run by its trace id and gives a rating and a note. Local only, gated exactly
like Settings and Rebuild.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from nl2sql import NL2SQL
from nl2sql.testing.facade import StubDatasources, StubLLMs
from nl2sql.api.query_api import QueryResult, SubQueryResult
from nl2sql.cli.demo.playground.app import build_app
from nl2sql.storage.files import FilesTraces
from nl2sql.storage.sqlite import SqliteState
from nl2sql.testing.config import make_config

TRACE_ID = "0b8f7d2e-1111-4222-8333-944455556666"


class _Engine(NL2SQL):
    """The real facade over an empty context; only ``run_query`` is scripted."""

    def __init__(self, data, feedback=True):
        config = make_config(data / "config", settings={"feedback": {"enabled": feedback}})
        state = SqliteState(data / "state.db")
        state.migrate()
        self._runtime = type("Ctx", (), {"state": state, "vector_store": None, "ds_registry": None,
                                         "traces": FilesTraces(data / "traces"),
                                         "config": config})()

    @property
    def datasources(self):
        return StubDatasources(getattr(self, "_runtime", None), [])

    @property
    def llm(self):
        return StubLLMs({})

    def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None,
                  cancellation_token=None):
        return QueryResult(trace_id=TRACE_ID, status="success", sub_queries=[SubQueryResult(
            id="sq1", sql="SELECT COUNT(*) FROM Customer", status="success")])


def _client(tmp_path, host="127.0.0.1", allow_settings=False, project=True, feedback=True):
    app = build_app(_Engine(tmp_path / "data", feedback=feedback), questions=[], roles=["admin", "viewer"], mode="replay", dataset="chinook",
                    project_dir=tmp_path if project else None, host=host, allow_settings=allow_settings)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _ask_and_rate(client, rating="down", note="wrong number"):
    client.post("/api/ask", json={"question": "How many customers?", "role": "viewer"})
    return client.post("/api/feedback", json={"trace_id": TRACE_ID, "rating": rating, "note": note})


def test_a_rating_is_stored_with_what_the_server_answered(tmp_path):
    client = _client(tmp_path)

    response = _ask_and_rate(client)

    assert response.status_code == 200, response.text
    assert response.json()["saved"]["rating"] == "down"
    store = SqliteState(tmp_path / "data" / "state.db")
    [row] = store.list_feedback()
    store.close()
    assert row["trace_id"] == TRACE_ID and row["question"] == "How many customers?"
    assert row["role"] == "viewer" and row["status"] == "success"
    assert row["sql"] == ["SELECT COUNT(*) FROM Customer"] and row["note"] == "wrong number"
    assert row["engine_version"]


def test_the_list_returns_the_ratings_and_the_counts(tmp_path):
    client = _client(tmp_path)
    _ask_and_rate(client, rating="up", note=None)

    body = client.get("/api/feedback").json()

    assert body["available"] is True and body["reason"] is None
    assert body["counts"] == {"up": 1, "down": 0}
    assert [e["trace_id"] for e in body["entries"]] == [TRACE_ID]


def test_rating_a_run_the_server_did_not_answer_is_refused(tmp_path):
    response = _client(tmp_path).post("/api/feedback", json={"trace_id": TRACE_ID, "rating": "up"})

    assert response.status_code == 404


def test_a_bad_rating_or_a_long_note_is_refused(tmp_path):
    client = _client(tmp_path)
    client.post("/api/ask", json={"question": "q", "role": "admin"})

    assert client.post("/api/feedback", json={"trace_id": TRACE_ID, "rating": "meh"}).status_code == 422
    assert client.post("/api/feedback", json={"trace_id": TRACE_ID, "rating": "up",
                                              "note": "x" * 281}).status_code == 422


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20"])
def test_feedback_is_refused_on_a_remote_bind_without_the_flag(tmp_path, host):
    client = _client(tmp_path, host=host)

    read = client.get("/api/feedback").json()
    response = _ask_and_rate(client)

    assert read["available"] is False and "entries" not in read
    assert "--allow-settings" in read["reason"]
    assert response.status_code == 403
    assert SqliteState(tmp_path / "data" / "state.db").list_feedback() == []


def test_feedback_is_allowed_on_a_remote_bind_with_the_flag(tmp_path):
    client = _client(tmp_path, host="0.0.0.0", allow_settings=True)

    assert _ask_and_rate(client).status_code == 200
    assert client.get("/api/feedback").json()["counts"] == {"up": 0, "down": 1}


def test_a_cross_site_post_cannot_rate(tmp_path):
    client = _client(tmp_path)
    client.post("/api/ask", json={"question": "q", "role": "admin"})

    other = client.post("/api/feedback", json={"trace_id": TRACE_ID, "rating": "up"},
                        headers={"Origin": "http://evil.example"})
    form = client.post("/api/feedback", content="trace_id=x&rating=up",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})

    assert other.status_code == 403
    assert form.status_code in (415, 422)


def test_feedback_is_off_outside_a_demo_project(tmp_path):
    client = _client(tmp_path, project=False)

    assert client.get("/api/feedback").json()["available"] is False
    assert _ask_and_rate(client).status_code == 403


def test_the_setting_turns_feedback_off(tmp_path):
    client = _client(tmp_path, feedback=False)

    read = client.get("/api/feedback").json()

    assert read["available"] is False and "settings.feedback.enabled" in read["reason"]
    assert _ask_and_rate(client).status_code == 403
