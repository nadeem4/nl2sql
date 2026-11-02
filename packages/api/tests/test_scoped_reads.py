"""Reads over a real engine: a datasource the caller's scope hides is not found, whichever route asks (no existence
oracle), and a non-admin never gets a driver's text (docs/architecture/wave_d.md, row A3)."""
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, Embeddings, UserContext
from nl2sql.testing.config import make_config
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.jobs import Jobs
from nl2sql_api.main import app

API = "/api/v1"
RAW = "FATAL: password authentication failed for user nl2sql at db.internal"
ROLES = {"roles": {"analyst": {"description": "Reads shop", "allow": ["shop"]},
                   "admin": {"description": "Everything", "allow": ["*"]}}}
DATASOURCES = [{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}},
               {"id": "hr", "type": "sqlite", "connection": {"database": "hr.db"}}]


@pytest.fixture
def real(tmp_path):
    for name in ("shop", "hr"):
        with sqlite3.connect(tmp_path / f"{name}.db") as connection:
            connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    cfg = make_config(tmp_path, datasources=DATASOURCES, policies=ROLES)
    engine = NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))}))
    who = {"user": UserContext(user_id="ann", roles=["analyst"])}
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[identity.caller] = lambda: who["user"]
    client = TestClient(app, raise_server_exceptions=False)
    yield client, engine, who
    app.dependency_overrides.clear()
    client.close()
    engine.close()


ROUTES = [("/datasources/{}/schema", {}), ("/datasources/{}/diagram", {}),
          ("/knowledge", {"datasource_id": "{}"})]


@pytest.mark.parametrize("path,params", ROUTES)
def test_unknown_and_hidden_datasources_are_the_same_404(real, path, params):
    client, _, _ = real

    def ask(datasource_id):
        return client.get(API + path.format(datasource_id),
                          params={k: v.format(datasource_id) for k, v in params.items()})

    assert ask("shop").status_code == 200  # one the caller reaches
    hidden, unknown = ask("hr"), ask("nope")
    assert hidden.status_code == 404 and unknown.status_code == 404
    assert hidden.json()["error"]["code"] == unknown.json()["error"]["code"] == "NOT_FOUND"
    assert hidden.json()["error"]["message"].replace("hr", "x") == unknown.json()["error"]["message"].replace("nope", "x")


def test_index_health_is_filtered_and_has_no_driver_text_for_a_non_admin(real, monkeypatch):
    client, engine, who = real
    monkeypatch.setattr(engine._runtime.ds_registry, "unavailable", lambda: {"shop": RAW, "hr": RAW})

    mine = client.get(f"{API}/index").json()
    assert RAW not in str(mine) and "'hr'" not in str(mine)
    assert any("'shop'" in w and "unavailable" in w for w in mine["warnings"])

    who["user"] = UserContext(user_id="root", roles=["admin"])
    full = client.get(f"{API}/index").json()
    assert RAW in str(full) and "'hr'" in str(full)


def test_a_failed_datasources_reason_is_masked_for_a_non_admin(real, monkeypatch):
    client, engine, who = real
    monkeypatch.setattr(engine._runtime.ds_registry, "unavailable", lambda: {"shop": RAW})

    mine = client.get(f"{API}/datasources").json()
    assert [d["id"] for d in mine] == ["shop"] and mine[0]["status"] == "failed"
    assert RAW not in str(mine) and RAW not in client.get(f"{API}/datasources/shop").text

    who["user"] = UserContext(user_id="root", roles=["admin"])
    assert client.get(f"{API}/datasources/shop").json()["reason"] == RAW


def test_a_failed_index_is_logged_once(real, caplog):
    client, engine, who = real
    app.state.jobs = Jobs(poll_s=0.01)
    who["user"] = UserContext(user_id="root", roles=["admin"])
    with caplog.at_level(logging.ERROR, logger="nl2sql_api"):
        job = client.post(f"{API}/jobs/index", json={"datasource_ids": ["nope"]}).json()
        import time
        for _ in range(500):
            if client.get(f"{API}/jobs/{job['id']}").json()["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.01)
    app.state.jobs.close()
    del app.state.jobs
    assert len([r for r in caplog.records if r.name == "nl2sql_api" and "nope" in r.getMessage()]) == 1
