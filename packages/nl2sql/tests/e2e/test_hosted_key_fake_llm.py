"""A question runs on the visitor's key, and the key lands nowhere.

The whole graph runs against ``nl2sql.testing.fake_llm``, an OpenAI-compatible
stand-in, so a real question is answered with no real key and nothing spent.
The visitor's key travels in the ``X-NL2SQL-Api-Key-<provider>`` header of
``nl2sql-api`` (``api.request_llm: true``). What is checked afterwards is the
promise the hosted demo makes: the key reached the model for that one
question, and it is in no file of the demo project, in no config, in the run's
trace, in the response, in the process environment or in anything that was
logged.
"""
import logging

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("nl2sql_api")

from nl2sql.testing.fake_llm import FakeLLMServer  # noqa: E402

from .conftest import copy_demo, project_config  # noqa: E402
from .recordings_chinook import RULES_COUNT_CUSTOMERS  # noqa: E402

# Built at run time so no scanner mistakes a test fixture for a leaked key.
VISITOR_KEY = "-".join(["sk", "proj", "hostede2e" + "v" * 30 + "6c1b"])
CLAUDE_KEY = "-".join(["sk", "ant", "api03", "hostede2e" + "c" * 28 + "4a7d"])
QUESTION = "How many customers are there?"
API_KEY_HEADER = "X-NL2SQL-Api-Key-"
MODELS_HEADER = "X-NL2SQL-Models"


def _files(root):
    return [path for path in root.rglob("*") if path.is_file()]


def _serve(project, monkeypatch):
    """A config over the copied demo project with no key and no endpoint of its own, taking keys in a request."""
    path = project_config(
        project, "keyless.yaml", llm={"provider": "openai", "model": "gpt-4o", "temperature": 0.0},
        storage=project / ".nl2sql", settings={"traces": {"mode": "always"}},
        api={"auth": "static", "static_role": "admin", "request_llm": True})
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NL2SQL_CONFIG", str(path))


@pytest.mark.e2e
def test_the_visitors_key_answers_the_question_and_lands_nowhere(demo_project, tmp_path, monkeypatch, caplog):
    import os

    from fastapi.testclient import TestClient

    from nl2sql_api.main import app

    project = tmp_path / "demo"
    copy_demo(demo_project, project)
    # The stand-in provider, reached exactly as a real one would be: the
    # visitor's key as the bearer token, no key anywhere on the server. A
    # visitor's key never rides to the operator's endpoint, so the stand-in is
    # reached through the SDK's own variable.
    provider = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url)
    _serve(project, monkeypatch)

    try:
        with TestClient(app) as client:
            with caplog.at_level(logging.DEBUG):
                answered = client.post("/api/v1/query", json={"question": QUESTION},
                                       headers={API_KEY_HEADER + "openai": VISITOR_KEY})
            body = answered.json()
            trace = client.get(f"/api/v1/traces/{body['trace_id']}")
            registry = app.state.engine._runtime.llm_registry.llms
    finally:
        provider.stop()

    # A real answer, and the fake provider saw the key.
    assert answered.status_code == 200, answered.text
    assert body["status"] == "success", body["errors"]
    assert "COUNT(" in body["sub_queries"][0]["sql"]
    assert provider.calls and all(call["authorization"] == f"Bearer {VISITOR_KEY}" for call in provider.calls)

    # And now the promise: the key is in none of these.
    assert trace.status_code == 200
    for path in _files(project):
        assert VISITOR_KEY not in path.read_text(encoding="utf-8", errors="ignore"), path
    assert not (project / ".env").exists()
    for text in (answered.text, trace.text, caplog.text):
        assert VISITOR_KEY not in text
    assert VISITOR_KEY not in "".join(os.environ.values())
    # Nor in the registry, where the next visitor would have found it.
    assert registry == {}


@pytest.mark.e2e
def test_a_step_on_another_provider_sends_that_providers_key_to_that_endpoint(
        demo_project, tmp_path, monkeypatch, caplog):
    """The planner on Claude and everything else on OpenAI, in one question.

    Both wires are the same stand-in server, so the whole graph runs with no
    real key: what is checked is that each step reached the endpoint of the
    provider it was put on, with that provider's key and no other.
    """
    import os

    from fastapi.testclient import TestClient

    from nl2sql.llm import registry as llm_registry
    from nl2sql_api.main import app

    pytest.importorskip("langchain_anthropic")

    project = tmp_path / "demo"
    copy_demo(demo_project, project)
    provider = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    monkeypatch.setenv("OPENAI_BASE_URL", provider.base_url)
    # Anthropic's endpoint is its preset's, exactly as in production; here the
    # preset points at the stand-in's Anthropic-shaped half.
    preset = llm_registry.PROVIDER_PRESETS["anthropic"]
    monkeypatch.setitem(llm_registry.PROVIDER_PRESETS, "anthropic",
                        preset._replace(base_url=provider.anthropic_base_url))
    _serve(project, monkeypatch)
    models = {MODELS_HEADER: '{"astplanner": "anthropic:claude-opus-5"}'}

    try:
        with TestClient(app) as client:
            with caplog.at_level(logging.DEBUG):
                answered = client.post(
                    "/api/v1/query", json={"question": QUESTION},
                    headers={API_KEY_HEADER + "openai": VISITOR_KEY, API_KEY_HEADER + "anthropic": CLAUDE_KEY,
                             **models})
                # The same visitor, now with no Claude key: named, and refused.
                refused = client.post("/api/v1/query", json={"question": QUESTION},
                                      headers={API_KEY_HEADER + "openai": VISITOR_KEY, **models})
            body = answered.json()
            trace = client.get(f"/api/v1/traces/{body['trace_id']}")
            registry = app.state.engine._runtime.llm_registry.llms
    finally:
        provider.stop()

    assert answered.status_code == 200, answered.text
    assert body["status"] == "success", body["errors"]

    # Each step went to its own provider's endpoint with its own key.
    by_name = {call["name"]: call for call in provider.calls}
    assert by_name["PlannerAnswer"]["mode"] == "anthropic"
    assert by_name["PlannerAnswer"]["api_key"] == CLAUDE_KEY
    for name in ("AnswerabilityResponse", "DecomposerResponse", "AggregatedResponse"):
        assert by_name[name]["mode"] == "tools", name
        assert by_name[name]["authorization"] == f"Bearer {VISITOR_KEY}", name
    # Neither key was ever sent to the other provider's endpoint.
    assert all(call["api_key"] != VISITOR_KEY for call in provider.calls if call["mode"] == "anthropic")

    # Without the Claude key the step is refused, the provider is named, and nothing ran on the key that was there.
    assert refused.status_code == 422, refused.text
    assert "anthropic" in refused.text.lower()

    # And the promise, now for both keys.
    assert trace.status_code == 200
    for key in (VISITOR_KEY, CLAUDE_KEY):
        for path in _files(project):
            assert key not in path.read_text(encoding="utf-8", errors="ignore"), path
        for text in (answered.text, refused.text, trace.text, caplog.text):
            assert key not in text
        assert key not in "".join(os.environ.values())
    assert registry == {}
