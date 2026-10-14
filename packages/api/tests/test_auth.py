"""The RBAC role comes from the ``get_user_context`` dependency, never unverified from the body.

Sources, in order, all from the ``api`` section of ``nl2sql.yaml``: a header set
by a trusted proxy (``api.role_header``), then a static role (``api.role``). The
body's ``user_context`` counts only with the dev flag ``api.trust_body_role``.
No role is a 401.
"""
import logging

import pytest

from nl2sql import QueryResult


@pytest.fixture
def client(api_client):
    def _make(**api):
        return api_client(QueryResult(status="success"), authenticated=False, api=api)
    return _make


def _ask(client, **kwargs):
    return client.post("/api/v1/query", json={"natural_language": "q", **kwargs.pop("body", {})}, **kwargs)


def test_api_reads_role_source_from_config(client, monkeypatch):
    # The 0.4 variables are not read any more: only the api section counts.
    monkeypatch.setenv("NL2SQL_API_ROLE", "admin")
    monkeypatch.setenv("NL2SQL_API_TRUST_BODY_ROLE", "true")
    http, engine = client()
    assert _ask(http, body={"user_context": {"roles": ["admin"]}}).status_code == 401

    http, engine = client(role="viewer")
    assert _ask(http).status_code == 200
    assert engine.calls[0]["user_context"].roles == ["viewer"]


def test_no_configured_role_is_a_401_not_a_500(client):
    http, engine = client()
    response = _ask(http)
    assert response.status_code == 401
    assert "api.role" in response.json()["detail"]
    assert engine.calls == []


def test_the_body_role_is_ignored_by_default(client):
    http, engine = client()
    response = _ask(http, body={"user_context": {"roles": ["admin"]}})
    assert response.status_code == 401
    assert "api.trust_body_role" in response.json()["detail"]
    assert engine.calls == []


def test_the_role_comes_from_the_trusted_proxy_header(client):
    http, engine = client(role_header="X-NL2SQL-Role")
    response = _ask(http, headers={"X-NL2SQL-Role": "analyst, viewer"},
                    body={"user_context": {"roles": ["admin"]}})
    assert response.status_code == 200, response.text
    assert engine.calls[0]["user_context"].roles == ["analyst", "viewer"]


def test_a_configured_header_that_is_missing_is_a_401(client):
    http, _ = client(role_header="X-NL2SQL-Role")
    assert _ask(http).status_code == 401


def test_the_static_role_applies_when_no_header_carries_one(client):
    http, engine = client(role_header="X-NL2SQL-Role", role="viewer")
    assert _ask(http).status_code == 200
    assert engine.calls[0]["user_context"].roles == ["viewer"]


def test_the_dev_flag_trusts_the_body(client):
    http, engine = client(trust_body_role=True)
    response = _ask(http, body={"user_context": {"user_id": "u-1", "roles": ["admin"]}})
    assert response.status_code == 200, response.text
    assert engine.calls[0]["user_context"].user_id == "u-1"
    assert engine.calls[0]["user_context"].roles == ["admin"]


def test_the_dev_flag_still_needs_a_role(client):
    http, _ = client(trust_body_role=True)
    assert _ask(http).status_code == 401


@pytest.mark.parametrize("user_context", [None, {}, {"roles": []}, "not an object"],
                         ids=["null", "empty", "no-roles", "malformed"])
def test_a_body_with_no_role_is_a_401_not_a_500(client, user_context):
    http, engine = client(trust_body_role=True)
    response = _ask(http, body={"user_context": user_context})
    assert response.status_code == 401, response.text
    assert engine.calls == []


def test_the_dev_flag_logs_a_warning(caplog):
    from nl2sql.config import Api
    from nl2sql_api.auth import warn_if_body_role_trusted

    with caplog.at_level(logging.WARNING):
        warn_if_body_role_trusted(Api(trust_body_role=True))
    assert "api.trust_body_role" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        warn_if_body_role_trusted(Api())
    assert caplog.text == ""
