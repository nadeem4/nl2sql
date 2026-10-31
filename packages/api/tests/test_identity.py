"""Who is asking: ``api.auth`` picks one source and every request resolves one ``UserContext`` (D203, D205)."""
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError

from nl2sql import UserContext
from nl2sql.config import Api
from nl2sql.testing.config import make_config
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import create_app

KEYS = {
    "nl2sql_admin_secret": UserContext(user_id="ada", tenant_id="acme", roles=["admin"]),
    "nl2sql_viewer_secret": UserContext(user_id="vi", tenant_id="acme", roles=["viewer"]),
}


class _Keys:
    def verify(self, token):
        return KEYS.get(token)


@pytest.fixture
def make_client(tmp_path):
    """A client for an app with ``/me`` (any caller) and ``/admin-only``, over a stub engine whose config is real."""
    made = []

    def _make(peer="127.0.0.1", base_url="http://localhost:8000", **sections):
        config = make_config(tmp_path / f"project-{len(made)}", **sections)
        engine = SimpleNamespace(config=config, api_keys=_Keys())
        app = create_app()
        router = APIRouter()

        @router.get("/me")
        def me(user: UserContext = Depends(identity.caller)):
            return user

        @router.get("/admin-only")
        def admin_only(user: UserContext = Depends(identity.admin)):
            return user

        app.include_router(router, prefix="/test")
        app.dependency_overrides[get_engine] = lambda: engine
        client = TestClient(app, base_url=base_url, client=(peer, 50000))
        made.append(client)
        return client
    return _make


def _error(response):
    return response.json()["error"]


# ---------------------------------------------------------------- api_keys

def test_identity_api_keys(make_client):
    client = make_client(api={"auth": "api_keys"})
    response = client.get("/test/me", headers={"Authorization": "Bearer nl2sql_admin_secret"})
    assert response.status_code == 200
    assert response.json() == {"user_id": "ada", "tenant_id": "acme", "roles": ["admin"]}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}],
                         ids=["missing", "unknown", "not-bearer"])
def test_api_keys_refuses_a_missing_or_bad_key(make_client, headers):
    response = make_client(api={"auth": "api_keys"}).get("/test/me", headers=headers)
    assert response.status_code == 401
    assert _error(response)["code"] == "UNAUTHENTICATED"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_api_keys_admin_needs_an_admin_role(make_client):
    client = make_client(api={"auth": "api_keys"})
    assert client.get("/test/admin-only", headers={"Authorization": "Bearer nl2sql_admin_secret"}).status_code == 200
    response = client.get("/test/admin-only", headers={"Authorization": "Bearer nl2sql_viewer_secret"})
    assert response.status_code == 403
    assert _error(response)["code"] == "FORBIDDEN"
    assert "admin_roles" in _error(response)["fix"]


# ---------------------------------------------------------------- proxy

PROXY = {"auth": "proxy", "proxy": {"user_header": "X-User", "tenant_header": "X-Tenant", "roles_header": "X-Roles",
                                    "trusted_from": ["10.0.0.0/8"]}}


def test_identity_proxy_headers(make_client):
    client = make_client(peer="10.1.2.3", api=PROXY)
    response = client.get("/test/me", headers={"X-User": "bo", "X-Tenant": "globex", "X-Roles": "analyst, viewer"})
    assert response.json() == {"user_id": "bo", "tenant_id": "globex", "roles": ["analyst", "viewer"]}


def test_proxy_tenant_falls_back_to_the_settings_tenant(make_client):
    api = {**PROXY, "proxy": {**PROXY["proxy"], "tenant_header": None}}
    client = make_client(peer="10.1.2.3", api=api, settings={"tenant_id": "only-tenant"})
    response = client.get("/test/me", headers={"X-User": "bo", "X-Tenant": "ignored", "X-Roles": "viewer"})
    assert response.json()["tenant_id"] == "only-tenant"


def test_proxy_headers_from_an_untrusted_peer_are_401(make_client):
    client = make_client(peer="203.0.113.7", api=PROXY)
    response = client.get("/test/me", headers={"X-User": "bo", "X-Roles": "admin"})
    assert response.status_code == 401
    assert _error(response)["code"] == "UNAUTHENTICATED"


def test_proxy_without_a_user_header_is_401(make_client):
    response = make_client(peer="10.1.2.3", api=PROXY).get("/test/me", headers={"X-Roles": "admin"})
    assert response.status_code == 401


def test_proxy_admin_follows_the_roles(make_client):
    client = make_client(peer="10.1.2.3", api=PROXY)
    assert client.get("/test/admin-only", headers={"X-User": "a", "X-Roles": "admin"}).status_code == 200
    assert client.get("/test/admin-only", headers={"X-User": "a", "X-Roles": "viewer"}).status_code == 403


# ---------------------------------------------------------------- static

def test_identity_static(make_client):
    client = make_client(api={"auth": "static", "static_role": "analyst"})
    assert client.get("/test/me").json() == {"user_id": "static", "tenant_id": None, "roles": ["analyst"]}
    assert client.get("/test/admin-only").status_code == 403
    admin = make_client(api={"auth": "static", "static_role": "admin"})
    assert admin.get("/test/admin-only").status_code == 200


def test_static_without_a_role_is_refused(make_client):
    response = make_client(api={"auth": "static"}).get("/test/me")
    assert response.status_code == 401
    assert "static_role" in _error(response)["fix"]


# ---------------------------------------------------------------- none (D205)

def test_none_refuses_to_start_off_loopback():
    for host in ("0.0.0.0", "10.0.0.5", "example.com", ""):
        with pytest.raises(identity.StartRefused, match="api.auth"):
            identity.check_bind("none", host)
    for host in ("127.0.0.1", "::1", "localhost"):
        identity.check_bind("none", host)
    identity.check_bind("api_keys", "0.0.0.0")


def test_none_loopback_request_is_the_operator(make_client):
    for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost"):
        response = make_client().get("/test/admin-only", headers={"Host": host})
        assert response.status_code == 200, host
        assert response.json() == {"user_id": "local", "tenant_id": None, "roles": ["admin"]}


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
def test_none_refuses_proxied_request(make_client, header):
    response = make_client().get("/test/me", headers={header: "203.0.113.7"})
    assert response.status_code == 403
    assert _error(response)["code"] == "FORBIDDEN"
    assert "api.auth" in _error(response)["fix"]


def test_none_refuses_foreign_host_header(make_client):
    for base_url in ("http://evil.example", "http://203.0.113.7:8000", "http://localhost.evil.example"):
        response = make_client(base_url=base_url).get("/test/me")
        assert response.status_code == 403, base_url


def test_none_refuses_a_non_loopback_peer(make_client):
    response = make_client(peer="203.0.113.7").get("/test/me", headers={"Host": "localhost"})
    assert response.status_code == 403
    assert _error(response)["code"] == "FORBIDDEN"
    assert make_client(peer="::1").get("/test/me").status_code == 200


# ---------------------------------------------------------------- the old settings are gone

@pytest.mark.parametrize("field,value", [("trust_body_role", True), ("role_header", "X-Role"), ("role", "admin")])
def test_trust_body_role_is_gone(field, value):
    with pytest.raises(ValidationError):
        Api(**{field: value})


def test_the_old_auth_module_is_gone():
    with pytest.raises(ImportError):
        __import__("nl2sql_api.auth")
