"""The RBAC role comes from the ``get_user_context`` dependency, never unverified from the body.

Sources, in order: a header set by a trusted proxy (``NL2SQL_API_ROLE_HEADER``),
then a static role (``NL2SQL_API_ROLE``). The body's ``user_context`` counts only
with the dev flag ``NL2SQL_API_TRUST_BODY_ROLE=true``. No role is a 401.
"""
import logging

import pytest

from nl2sql import QueryResult

ENV_VARS = ("NL2SQL_API_ROLE_HEADER", "NL2SQL_API_ROLE", "NL2SQL_API_TRUST_BODY_ROLE")


@pytest.fixture
def client(api_client, monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return api_client(QueryResult(status="success"), authenticated=False)


def _ask(client, **kwargs):
    return client.post("/api/v1/query", json={"natural_language": "q", **kwargs.pop("body", {})}, **kwargs)


def test_no_configured_role_is_a_401_not_a_500(client):
    http, engine = client
    response = _ask(http)
    assert response.status_code == 401
    assert "NL2SQL_API_ROLE" in response.json()["detail"]
    assert engine.calls == []


def test_the_body_role_is_ignored_by_default(client):
    http, engine = client
    response = _ask(http, body={"user_context": {"roles": ["admin"]}})
    assert response.status_code == 401
    assert "NL2SQL_API_TRUST_BODY_ROLE" in response.json()["detail"]
    assert engine.calls == []


def test_the_role_comes_from_the_trusted_proxy_header(client, monkeypatch):
    monkeypatch.setenv("NL2SQL_API_ROLE_HEADER", "X-NL2SQL-Role")
    http, engine = client
    response = _ask(http, headers={"X-NL2SQL-Role": "analyst, viewer"},
                    body={"user_context": {"roles": ["admin"]}})
    assert response.status_code == 200, response.text
    assert engine.calls[0]["user_context"].roles == ["analyst", "viewer"]


def test_a_configured_header_that_is_missing_is_a_401(client, monkeypatch):
    monkeypatch.setenv("NL2SQL_API_ROLE_HEADER", "X-NL2SQL-Role")
    http, _ = client
    assert _ask(http).status_code == 401


def test_the_static_role_applies_when_no_header_carries_one(client, monkeypatch):
    monkeypatch.setenv("NL2SQL_API_ROLE_HEADER", "X-NL2SQL-Role")
    monkeypatch.setenv("NL2SQL_API_ROLE", "viewer")
    http, engine = client
    assert _ask(http).status_code == 200
    assert engine.calls[0]["user_context"].roles == ["viewer"]


def test_the_dev_flag_trusts_the_body(client, monkeypatch):
    monkeypatch.setenv("NL2SQL_API_TRUST_BODY_ROLE", "true")
    http, engine = client
    response = _ask(http, body={"user_context": {"user_id": "u-1", "roles": ["admin"]}})
    assert response.status_code == 200, response.text
    assert engine.calls[0]["user_context"].user_id == "u-1"
    assert engine.calls[0]["user_context"].roles == ["admin"]


def test_the_dev_flag_still_needs_a_role(client, monkeypatch):
    monkeypatch.setenv("NL2SQL_API_TRUST_BODY_ROLE", "true")
    http, _ = client
    assert _ask(http).status_code == 401


def test_the_dev_flag_logs_a_warning(monkeypatch, caplog):
    from nl2sql_api.auth import warn_if_body_role_trusted

    monkeypatch.setenv("NL2SQL_API_TRUST_BODY_ROLE", "true")
    with caplog.at_level(logging.WARNING):
        warn_if_body_role_trusted()
    assert "NL2SQL_API_TRUST_BODY_ROLE" in caplog.text

    caplog.clear()
    monkeypatch.delenv("NL2SQL_API_TRUST_BODY_ROLE")
    with caplog.at_level(logging.WARNING):
        warn_if_body_role_trusted()
    assert caplog.text == ""
