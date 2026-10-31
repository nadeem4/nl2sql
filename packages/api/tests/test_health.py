"""``/health``, ``/ready``, ``/meta`` and ``/about`` (docs/architecture/wave_d.md, "Health and readiness")."""
from importlib.metadata import version


def test_health_is_liveness_with_the_versions(api_client):
    client, _ = api_client()
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": {"engine": version("nl2sql-engine"),
                                                           "api": version("nl2sql-api")}}


def test_ready_keeps_todays_answer(api_client):
    client, _ = api_client()
    assert client.get("/api/v1/ready").json() == {"success": True, "data": None, "message": "NL2SQL API is ready"}


def test_meta_needs_no_caller_and_names_the_mode_and_identity_source(api_client):
    client, _ = api_client(authenticated=False, api={"auth": "api_keys", "request_llm": True}, mode="demo")
    response = client.get("/api/v1/meta")
    assert response.status_code == 200
    assert response.json() == {"mode": "demo", "auth": "api_keys", "request_llm": True}


def test_about_is_the_engines_description(api_client):
    client, engine = api_client()
    engine.about = lambda: {"version": "1.2.3", "steps": []}
    assert client.get("/api/v1/about").json() == {"version": "1.2.3", "steps": []}
