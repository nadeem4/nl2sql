"""PUT /api/v1/settings/llm replaces the engine's whole ``llm`` section through ``engine.llm.replace``.

docs/architecture/configuration_v2.md, "Replacing the LLM at runtime": admin
only, the body is the ``llm`` section as JSON, a bad section is a 422 with the
config error's text, and the answer is the change with no keys.
"""
import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, Embeddings
from nl2sql.testing.config import make_config
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

ROUTE = "/api/v1/settings/llm"
NEW_LLM = {"provider": "openai", "model": "gpt-other", "api_key": "sk-test",
           "agents": {"ast_planner": {"model": "gpt-planner"}}}


@pytest.fixture
def make_client(tmp_path):
    made = []

    def _make(host="127.0.0.1", **sections):
        cfg = make_config(tmp_path / f"project-{len(made)}",
                          llm={"provider": "openai", "model": "gpt-test", "api_key": "sk-test"}, **sections)
        engine = NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))}))
        app.dependency_overrides[get_engine] = lambda: engine
        client = TestClient(app, client=(host, 50000))
        made.append((client, engine))
        return client, engine

    yield _make
    app.dependency_overrides.clear()
    for client, engine in made:
        client.close()
        engine.close()


def test_put_settings_llm_requires_admin(make_client):
    client, engine = make_client(api={"role_header": "X-NL2SQL-Role"})

    assert client.put(ROUTE, json=NEW_LLM).status_code == 401
    assert client.put(ROUTE, json=NEW_LLM, headers={"X-NL2SQL-Role": "analyst"}).status_code == 403
    assert engine.config.llm.model == "gpt-test"

    response = client.put(ROUTE, json=NEW_LLM, headers={"X-NL2SQL-Role": "admin"})
    assert response.status_code == 200, response.text
    assert engine.config.llm.model == "gpt-other"


def test_put_settings_llm_from_a_remote_client_without_a_role_source_is_refused(make_client):
    client, engine = make_client(host="203.0.113.7")

    assert client.put(ROUTE, json=NEW_LLM).status_code == 403
    assert engine.config.llm.model == "gpt-test"


def test_put_settings_llm_returns_the_change_without_keys(make_client):
    client, _ = make_client(api={"role_header": "X-NL2SQL-Role"})

    response = client.put(ROUTE, json=NEW_LLM, headers={"X-NL2SQL-Role": "admin"})

    body = response.json()
    assert body["by"] == {"user_id": None, "roles": ["admin"], "via": "api"}
    assert body["changes"]["default"] == {"old": "openai/gpt-test", "new": "openai/gpt-other"}
    assert body["changes"]["ast_planner"] == {"old": "openai/gpt-test", "new": "openai/gpt-planner"}
    assert "sk-test" not in response.text


def test_put_settings_llm_returns_422_with_the_config_error(make_client):
    client, engine = make_client()

    response = client.put(ROUTE, json={"provider": "openai", "model": "gpt-other", "agents": {"ast_planer": {}}})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "llm.agents.ast_planer" in detail and "Did you mean 'ast_planner'?" in detail
    assert engine.config.llm.model == "gpt-test"


def test_post_llm_is_gone(make_client):
    client, _ = make_client()

    assert client.post("/api/v1/llm", json={"config": {"name": "default"}}).status_code == 405
