"""The admin routes (anything that changes datasources, LLMs or the index) need an admin role.

The role comes from the same sources as ``/query`` (``nl2sql_api.auth``, the
``api`` section of ``nl2sql.yaml``). A request is admin when one of its roles is
in ``policies.admin_roles`` (default ``admin``). With no role source configured
at all, admin routes answer loopback clients only.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nl2sql import DatasourceStatus, LLMChange

from nl2sql.testing.config import make_config
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

DATASOURCE = {"id": "ds", "type": "sqlite", "connection": {"database": "ds.db"}}

ADMIN_ROUTES = [
    ("post", "/api/v1/datasource", {"config": DATASOURCE}),
    ("post", "/api/v1/datasource", {"config": DATASOURCE, "save": True}),
    ("post", "/api/v1/datasource/test", {"config": DATASOURCE}),
    ("get", "/api/v1/datasource-types", None),
    ("delete", "/api/v1/datasource/ds", None),
    ("put", "/api/v1/settings/llm", {"provider": "openai", "model": "gpt-test"}),
    ("post", "/api/v1/index/ds", None),
    ("post", "/api/v1/index-all", None),
    ("delete", "/api/v1/index", None),
]
ADMIN_IDS = [f"{method.upper()} {path}" for method, path, _ in ADMIN_ROUTES]

READ_ONLY_ROUTES = ["/api/v1/health", "/api/v1/datasource", "/api/v1/index/status"]


class _Recorder:
    """One namespace of the fake engine: each listed call is recorded, then answered."""

    def __init__(self, engine, answers):
        self._engine = engine
        self._answers = answers

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self._engine.calls.append(name)
            answer = self._answers[name]
            if isinstance(answer, Exception):
                raise answer
            return answer() if callable(answer) else answer
        return call


class AdminEngine:
    """Records every admin call so a refused request can be shown to do nothing."""

    def __init__(self, config):
        self.config = config
        self.calls = []
        self.datasources = _Recorder(self, {
            "add": None, "remove": None, "list": list, "types": list, "save": Path("datasources/ds.yaml"),
            "test": DatasourceStatus(id="ds", status="ready")})
        self.llm = _Recorder(self, {"replace": lambda: LLMChange()})
        self.index = _Recorder(self, {"build": lambda: SimpleNamespace(ok=True, stats=[], empty=[], errors=[]),
                                      "clear": None, "health": lambda: {"status": "ok"}})


@pytest.fixture
def make_client(tmp_path):
    clients = []

    def _make(host="testclient", **sections):
        engine = AdminEngine(make_config(tmp_path / f"project-{len(clients)}", **sections))
        app.dependency_overrides[get_engine] = lambda: engine
        client = TestClient(app, client=(host, 50000))
        clients.append(client)
        return client, engine

    yield _make
    app.dependency_overrides.clear()
    for client in clients:
        client.close()


def _call(client, method, path, body, headers=None):
    kwargs = {"headers": headers or {}}
    if body is not None:
        kwargs["json"] = body
    return client.request(method.upper(), path, **kwargs)


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES, ids=ADMIN_IDS)
def test_no_role_source_refuses_a_remote_client(make_client, method, path, body):
    client, engine = make_client(host="203.0.113.7")
    response = _call(client, method, path, body)
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    for name in ("api.role_header", "api.role", "policies.admin_roles"):
        assert name in detail
    assert engine.calls == []


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES, ids=ADMIN_IDS)
def test_no_role_source_allows_a_loopback_client(make_client, method, path, body, host):
    client, _ = make_client(host=host)
    response = _call(client, method, path, body)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES, ids=ADMIN_IDS)
def test_a_role_that_is_not_admin_is_a_403_even_from_loopback(make_client, method, path, body):
    client, engine = make_client(host="127.0.0.1", api={"role": "analyst"})
    response = _call(client, method, path, body)
    assert response.status_code == 403, response.text
    assert "policies.admin_roles" in response.json()["detail"]
    assert engine.calls == []


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES, ids=ADMIN_IDS)
def test_the_admin_role_is_accepted(make_client, method, path, body):
    client, _ = make_client(host="203.0.113.7", api={"role_header": "X-NL2SQL-Role"})
    response = _call(client, method, path, body, headers={"X-NL2SQL-Role": "viewer, admin"})
    assert response.status_code == 200, response.text


def test_a_configured_header_that_is_missing_is_a_401(make_client):
    client, engine = make_client(host="127.0.0.1", api={"role_header": "X-NL2SQL-Role"})
    assert client.post("/api/v1/index-all").status_code == 401
    assert engine.calls == []


def test_the_admin_roles_come_from_the_policies(make_client, monkeypatch):
    # NL2SQL_API_ADMIN_ROLES is not read any more: policies.admin_roles is the list.
    monkeypatch.setenv("NL2SQL_API_ADMIN_ROLES", "admin")
    client, _ = make_client(api={"role_header": "X-NL2SQL-Role"},
                            policies={"allow_all": True, "admin_roles": ["ops", "owner"]})
    assert client.post("/api/v1/index-all", headers={"X-NL2SQL-Role": "owner"}).status_code == 200
    assert client.post("/api/v1/index-all", headers={"X-NL2SQL-Role": "admin"}).status_code == 403


def test_the_dev_flag_takes_the_admin_role_from_the_body(make_client):
    client, engine = make_client(api={"trust_body_role": True})
    body = {"config": DATASOURCE, "user_context": {"roles": ["admin"]}}
    assert client.post("/api/v1/datasource", json=body).status_code == 200
    assert engine.calls == ["add"]


@pytest.mark.parametrize("path", READ_ONLY_ROUTES)
def test_read_only_routes_stay_open(make_client, path):
    client, _ = make_client(host="203.0.113.7")
    assert client.get(path).status_code == 200


def test_a_failed_add_datasource_does_not_echo_the_exception(make_client):
    client, engine = make_client(host="127.0.0.1")
    engine.datasources._answers["add"] = RuntimeError(
        "could not connect to postgresql://app:hunter2@db.internal:5432/sales")
    response = client.post("/api/v1/datasource", json={"config": {**DATASOURCE, "id": "sales"}})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "hunter2" not in detail and "db.internal" not in detail
    assert "server log" in detail
