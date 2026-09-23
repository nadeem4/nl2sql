"""A hosted question runs on the visitor's key, and the key lands nowhere.

The whole graph runs against ``nl2sql.testing.fake_llm``, an OpenAI-compatible
stand-in, so a real question is answered with no real key and nothing spent.
What is checked afterwards is the promise the hosted demo makes: the key
reached the model for that one question, and it is in no file of the demo
project, in no config, in the run's trace, in the response, in the process
environment or in anything that was logged.
"""
import logging
import shutil

import pytest

pytest.importorskip("fastapi")

from nl2sql.cli.demo.llm_config import point_llm_config_at  # noqa: E402
from nl2sql.testing.fake_llm import FakeLLMServer  # noqa: E402

from .recordings_chinook import RULES_COUNT_CUSTOMERS  # noqa: E402

# Built at run time so no scanner mistakes a test fixture for a leaked key.
VISITOR_KEY = "-".join(["sk", "proj", "hostede2e" + "v" * 30 + "6c1b"])
CLAUDE_KEY = "-".join(["sk", "ant", "api03", "hostede2e" + "c" * 28 + "4a7d"])
QUESTION = "How many customers are there?"


def _files(root):
    return [path for path in root.rglob("*") if path.is_file()]


@pytest.mark.e2e
def test_the_visitors_key_answers_the_question_and_lands_nowhere(demo_project, tmp_path,
                                                                 monkeypatch, caplog):
    import os

    from fastapi.testclient import TestClient

    from nl2sql import NL2SQL
    from nl2sql.cli.demo.playground.app import build_app
    from nl2sql.cli.demo.playground.hosted import KEY_HEADER, Hosted
    from nl2sql.common.settings import reload_settings

    project = tmp_path / "demo"
    shutil.copytree(demo_project, project)
    # The stand-in provider, reached exactly as a real one would be: the
    # visitor's key as the bearer token, no key anywhere on the server.
    provider = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    point_llm_config_at(project, provider.base_url)

    monkeypatch.chdir(project)
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("LLM_CONFIG", "configs/llm.demo.yaml")
    monkeypatch.setenv("TRACE_MODE", "always")
    monkeypatch.setenv("TRACE_DIR", str(project / "traces"))
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    reload_settings()

    try:
        engine = NL2SQL()
        app = build_app(engine, questions=[QUESTION], roles=["admin"], mode="hosted",
                        dataset="chinook", trace_dir=project / "traces", project_dir=project,
                        host="0.0.0.0", hosted=Hosted(enabled=True))
        client = TestClient(app, base_url="http://demo.example:7860")

        with caplog.at_level(logging.DEBUG):
            refused = client.post("/api/ask", json={"question": QUESTION, "role": "admin"})
            answered = client.post("/api/ask", json={"question": QUESTION, "role": "admin"},
                                   headers={KEY_HEADER: VISITOR_KEY})
        body = answered.json()
        trace = client.get(f"/api/trace/{body['trace_id']}")
    finally:
        provider.stop()
        reload_settings()

    # Without a key: one clear sentence, no model call, no crash.
    assert refused.status_code == 401
    assert "Settings" in refused.json()["detail"]

    # With the visitor's key: a real answer, and the fake provider saw the key.
    assert answered.status_code == 200, answered.text
    assert body["status"] == "success", body["errors"]
    assert "COUNT(" in body["sub_queries"][0]["sql"]
    assert provider.calls and all(call["authorization"] == f"Bearer {VISITOR_KEY}"
                                  for call in provider.calls)

    # And now the promise: the key is in none of these.
    assert trace.status_code == 200
    for path in _files(project):
        assert VISITOR_KEY not in path.read_text(encoding="utf-8", errors="ignore"), path
    for name in ("configs/llm.demo.yaml", ".env.demo"):
        assert VISITOR_KEY not in (project / name).read_text(encoding="utf-8")
    for text in (answered.text, trace.text, caplog.text):
        assert VISITOR_KEY not in text
    assert VISITOR_KEY not in "".join(os.environ.values())
    # Nor in the registry, where the next visitor would have found it.
    assert engine.context.llm_registry.llms == {}


@pytest.mark.e2e
def test_a_step_on_another_provider_sends_that_providers_key_to_that_endpoint(
        demo_project, tmp_path, monkeypatch, caplog):
    """The planner on Claude and everything else on OpenAI, in one hosted question.

    Both wires are the same stand-in server, so the whole graph runs with no
    real key: what is checked is that each step reached the endpoint of the
    provider it was put on, with that provider's key and no other.
    """
    import os

    from fastapi.testclient import TestClient

    from nl2sql import NL2SQL
    from nl2sql.cli.demo.playground.app import build_app
    from nl2sql.cli.demo.playground.hosted import MODELS_HEADER, Hosted, key_header_for
    from nl2sql.common.settings import reload_settings
    from nl2sql.llm import registry as llm_registry

    pytest.importorskip("langchain_anthropic")

    project = tmp_path / "demo"
    shutil.copytree(demo_project, project)
    provider = FakeLLMServer(RULES_COUNT_CUSTOMERS).start()
    point_llm_config_at(project, provider.base_url)
    # Anthropic's endpoint is its preset's, exactly as in production; here the
    # preset points at the stand-in's Anthropic-shaped half.
    preset = llm_registry.PROVIDER_PRESETS["anthropic"]
    monkeypatch.setitem(llm_registry.PROVIDER_PRESETS, "anthropic",
                        preset._replace(base_url=provider.anthropic_base_url))

    monkeypatch.chdir(project)
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("LLM_CONFIG", "configs/llm.demo.yaml")
    monkeypatch.setenv("TRACE_MODE", "always")
    monkeypatch.setenv("TRACE_DIR", str(project / "traces"))
    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    reload_settings()

    try:
        engine = NL2SQL()
        app = build_app(engine, questions=[QUESTION], roles=["admin"], mode="hosted",
                        dataset="chinook", trace_dir=project / "traces", project_dir=project,
                        host="0.0.0.0", hosted=Hosted(enabled=True))
        client = TestClient(app, base_url="http://demo.example:7860")

        with caplog.at_level(logging.DEBUG):
            answered = client.post(
                "/api/ask", json={"question": QUESTION, "role": "admin"},
                headers={key_header_for("openai"): VISITOR_KEY,
                         key_header_for("anthropic"): CLAUDE_KEY,
                         MODELS_HEADER: '{"astplanner": "anthropic:claude-opus-5"}'})
            # The same visitor, now with no Claude key: named, and refused.
            refused = client.post(
                "/api/ask", json={"question": QUESTION, "role": "admin"},
                headers={key_header_for("openai"): VISITOR_KEY,
                         MODELS_HEADER: '{"astplanner": "anthropic:claude-opus-5"}'})
        body = answered.json()
        trace = client.get(f"/api/trace/{body['trace_id']}")
    finally:
        provider.stop()
        reload_settings()

    assert answered.status_code == 200, answered.text
    assert body["status"] == "success", body["errors"]

    # Each step went to its own provider's endpoint with its own key.
    by_name = {call["name"]: call for call in provider.calls}
    assert by_name["PlanModel"]["mode"] == "anthropic"
    assert by_name["PlanModel"]["api_key"] == CLAUDE_KEY
    for name in ("AnswerabilityResponse", "DecomposerResponse", "AggregatedResponse"):
        assert by_name[name]["mode"] == "tools", name
        assert by_name[name]["authorization"] == f"Bearer {VISITOR_KEY}", name
    # Neither key was ever sent to the other provider's endpoint.
    assert all(call["api_key"] != VISITOR_KEY for call in provider.calls
               if call["mode"] == "anthropic")

    # Without the Claude key the step is named, the provider is named, and
    # nothing ran on the key that was there.
    assert refused.status_code == 400
    assert "Query planner" in refused.json()["detail"]
    assert "Anthropic" in refused.json()["detail"]

    # And the promise, now for both keys.
    assert trace.status_code == 200
    for key in (VISITOR_KEY, CLAUDE_KEY):
        for path in _files(project):
            assert key not in path.read_text(encoding="utf-8", errors="ignore"), path
        for text in (answered.text, refused.text, trace.text, caplog.text):
            assert key not in text
        assert key not in "".join(os.environ.values())
    assert engine.context.llm_registry.llms == {}
