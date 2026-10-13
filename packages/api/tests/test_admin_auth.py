"""The admin routes (anything that changes datasources, LLMs or the index) need an admin role.

The role comes from the same sources as ``/query`` (``nl2sql_api.auth``). A request
is admin when one of its roles is in ``NL2SQL_API_ADMIN_ROLES`` (default ``admin``).
With no role source configured at all, admin routes answer loopback clients only.
"""
import pytest
from fastapi.testclient import TestClient

from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

ENV_VARS = ("NL2SQL_API_ROLE_HEADER", "NL2SQL_API_ROLE", "NL2SQL_API_TRUST_BODY_ROLE",
            "NL2SQL_API_ADMIN_ROLES")

ADMIN_ROUTES = [
    ("post", "/api/v1/datasource", {"config": {"id": "ds"}}),
    ("delete", "/api/v1/datasource/ds", None),
    ("post", "/api/v1/llm", {"config": {"name": "default"}}),
    ("post", "/api/v1/index/ds", None),
    ("post", "/api/v1/index-all", None),
    ("delete", "/api/v1/index", None),
]
ADMIN_IDS = [f"{method.upper()} {path}" for method, path, _ in ADMIN_ROUTES]

READ_ONLY_ROUTES = ["/api/v1/health", "/api/v1/datasource", "/api/v1/index/status"]


class AdminEngine:
    """Records every admin call so a refused request can be shown to do nothing."""

    def __init__(self):
        self.calls = []
        self.add_datasource_error = None
        self.indexing = self  # IndexingService calls engine.indexing.<method>

    def add_datasource(self, config):
        self.calls.append("add_datasource")
        if self.add_datasource_error:
            raise self.add_datasource_error

    def list_datasources(self):
        return ["ds"]

    def configure_llm(self, config):
        self.calls.append("configure_llm")

    def index_datasource(self, datasource_id):
        self.calls.append("index_datasource")
        return {}

    def index_all_datasources(self):
        self.calls.append("index_all_datasources")
        return {}

    def clear_index(self):
        self.calls.append("clear_index")
        return {"success": True}

    def index_health(self):
        return {"status": "ok"}


@pytest.fixture
def make_client(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    clients = []

    def _make(host="testclient"):
        engine = AdminEngine()
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
    for name in ("NL2SQL_API_ROLE_HEADER", "NL2SQL_API_ROLE", "NL2SQL_API_ADMIN_ROLES"):
        assert name in detail
    assert engine.calls == []


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES, ids=ADMIN_IDS)
def test_no_role_source_allows_a_loopback_client(make_client, method, path, body, host):
    client, _ = make_client(host=host)
    response = _call(client, method, path, body)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES, ids=ADMIN_IDS)
def test_a_role_that_is_not_admin_is_a_403_even_from_loopback(make_client, monkeypatch, method, path, body):
    monkeypatch.setenv("NL2SQL_API_ROLE", "analyst")
    client, engine = make_client(host="127.0.0.1")
    response = _call(client, method, path, body)
    assert response.status_code == 403, response.text
    assert "NL2SQL_API_ADMIN_ROLES" in response.json()["detail"]
    assert engine.calls == []


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES, ids=ADMIN_IDS)
def test_the_admin_role_is_accepted(make_client, monkeypatch, method, path, body):
    monkeypatch.setenv("NL2SQL_API_ROLE_HEADER", "X-NL2SQL-Role")
    client, _ = make_client(host="203.0.113.7")
    response = _call(client, method, path, body, headers={"X-NL2SQL-Role": "viewer, admin"})
    assert response.status_code == 200, response.text


def test_a_configured_header_that_is_missing_is_a_401(make_client, monkeypatch):
    monkeypatch.setenv("NL2SQL_API_ROLE_HEADER", "X-NL2SQL-Role")
    client, engine = make_client(host="127.0.0.1")
    assert client.post("/api/v1/index-all").status_code == 401
    assert engine.calls == []


def test_the_admin_roles_are_configurable(make_client, monkeypatch):
    monkeypatch.setenv("NL2SQL_API_ROLE_HEADER", "X-NL2SQL-Role")
    monkeypatch.setenv("NL2SQL_API_ADMIN_ROLES", "ops, owner")
    client, _ = make_client()
    assert client.post("/api/v1/index-all", headers={"X-NL2SQL-Role": "owner"}).status_code == 200
    assert client.post("/api/v1/index-all", headers={"X-NL2SQL-Role": "admin"}).status_code == 403


def test_the_dev_flag_takes_the_admin_role_from_the_body(make_client, monkeypatch):
    monkeypatch.setenv("NL2SQL_API_TRUST_BODY_ROLE", "true")
    client, engine = make_client()
    body = {"config": {"id": "ds"}, "user_context": {"roles": ["admin"]}}
    assert client.post("/api/v1/datasource", json=body).status_code == 200
    assert engine.calls == ["add_datasource"]


@pytest.mark.parametrize("path", READ_ONLY_ROUTES)
def test_read_only_routes_stay_open(make_client, path):
    client, _ = make_client(host="203.0.113.7")
    assert client.get(path).status_code == 200


def test_a_failed_add_datasource_does_not_echo_the_exception(make_client):
    client, engine = make_client(host="127.0.0.1")
    engine.add_datasource_error = RuntimeError(
        "could not connect to postgresql://app:hunter2@db.internal:5432/sales")
    response = client.post("/api/v1/datasource", json={"config": {"id": "sales"}})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "hunter2" not in detail and "db.internal" not in detail
    assert "server log" in detail
