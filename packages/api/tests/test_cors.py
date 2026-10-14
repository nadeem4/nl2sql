"""CORS configuration tests.

The allowed origins are ``api.cors_origins`` in ``nl2sql.yaml``, read from the
engine's config; ``NL2SQL_API_CORS_ORIGINS`` is not read any more.
"""
import pytest


@pytest.fixture
def app_with_origins(api_client):
    """A client whose engine's config allows ``origins``."""
    def _make(origins):
        client, _ = api_client(api={"cors_origins": origins})
        return client
    return _make


def _preflight(client, origin):
    return client.options(
        "/api/v1/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )


def test_no_configured_origins_rejects_arbitrary_origin(app_with_origins, monkeypatch):
    """Wildcard-with-credentials is unsafe; unset means no cross-origin access."""
    monkeypatch.setenv("NL2SQL_API_CORS_ORIGINS", "https://evil.example")
    client = app_with_origins([])

    response = _preflight(client, "https://evil.example")

    assert response.headers.get("access-control-allow-origin") != "*"
    assert response.headers.get("access-control-allow-origin") is None


def test_configured_origin_is_echoed_back(app_with_origins):
    client = app_with_origins(["https://bi.corp.example"])

    response = _preflight(client, "https://bi.corp.example")

    assert response.headers.get("access-control-allow-origin") == "https://bi.corp.example"


def test_unconfigured_origin_is_rejected_when_others_are_allowed(app_with_origins):
    client = app_with_origins(["https://bi.corp.example"])

    response = _preflight(client, "https://evil.example")

    assert response.headers.get("access-control-allow-origin") is None
