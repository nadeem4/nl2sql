"""CORS configuration tests.

The CORS middleware is attached at import time, so each case reloads
``nl2sql_api.main`` with the environment it wants and restores the module to its
default (no configured origins) afterwards.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import nl2sql_api.main

ENV_VAR = "NL2SQL_API_CORS_ORIGINS"


@pytest.fixture
def app_with_cors_env(monkeypatch):
    """Reload the app under a given ``NL2SQL_API_CORS_ORIGINS`` value."""
    clients = []

    def _make(origins):
        if origins is None:
            monkeypatch.delenv(ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(ENV_VAR, origins)
        module = importlib.reload(nl2sql_api.main)
        client = TestClient(module.app)
        clients.append(client)
        return client

    yield _make

    for client in clients:
        client.close()
    # Restore the module to the default configuration for any later test.
    monkeypatch.delenv(ENV_VAR, raising=False)
    importlib.reload(nl2sql_api.main)


def _preflight(client, origin):
    return client.options(
        "/api/v1/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )


def test_no_configured_origins_rejects_arbitrary_origin(app_with_cors_env):
    """Wildcard-with-credentials is unsafe; unset means no cross-origin access."""
    client = app_with_cors_env(None)

    response = _preflight(client, "https://evil.example")

    assert response.headers.get("access-control-allow-origin") != "*"
    assert response.headers.get("access-control-allow-origin") is None


def test_configured_origin_is_echoed_back(app_with_cors_env):
    client = app_with_cors_env("https://bi.corp.example")

    response = _preflight(client, "https://bi.corp.example")

    assert response.headers.get("access-control-allow-origin") == "https://bi.corp.example"


def test_unconfigured_origin_is_rejected_when_others_are_allowed(app_with_cors_env):
    client = app_with_cors_env("https://bi.corp.example")

    response = _preflight(client, "https://evil.example")

    assert response.headers.get("access-control-allow-origin") is None
