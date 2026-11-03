"""The settings routes: the LLM, the providers, API keys and the event catalog."""
from datetime import datetime, timezone

import pytest
from dashboard_stubs import Recorder

from nl2sql import ApiKey, EventInfo, LLMChange, UserContext

API = "/api/v1"
ADMIN = UserContext(roles=["admin"])
TOKEN = "nl2sql_" + "ab12cd34" + "_" + "s" * 32  # built at run time: no key-shaped literal in the source
KEY = ApiKey(id="ab12cd34", label="ci", user_id="ann", roles=["analyst"],
             created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), created_by="root")


@pytest.fixture
def stub(api_client):
    client, engine = api_client()
    engine.llm, engine.api_keys, engine.events = Recorder(), Recorder(), Recorder()
    return client, engine


def test_llm_lists_the_agents_and_the_one_agent_route_is_gone(stub):
    client, engine = stub
    engine.llm.returns["list"] = {"default": {"provider": "openai", "model": "gpt", "inherited": False}}

    assert client.get(f"{API}/llm").json() == {"llms": engine.llm.returns["list"]}
    assert client.get(f"{API}/llm/default").status_code == 404  # no alias: /llm/providers is the only /llm/<x>


def test_providers_route_has_no_key(stub):
    client, engine = stub
    engine.llm.returns["providers"] = [{"name": "openai", "key_variable": "OPENAI_API_KEY", "key_present": True,
                                        "models": ["gpt"], "key_shape": {"prefix": "sk-"}}]

    response = client.get(f"{API}/llm/providers")

    assert response.status_code == 200, response.text
    assert response.json()[0]["key_present"] is True and "api_key" not in response.text


def test_replace_llm_is_admin(api_client):
    client, engine = api_client(authenticated=False, api={"auth": "static", "static_role": "analyst"})
    engine.llm = Recorder()

    assert client.put(f"{API}/settings/llm", json={"provider": "openai", "model": "m"}).status_code == 403
    assert engine.llm.calls == []


def test_replace_llm_passes_the_caller_and_returns_the_change(stub):
    client, engine = stub
    engine.llm.returns["replace"] = LLMChange(by={"via": "api"}, changes={"default": {"old": "a/b", "new": "a/c"}})

    response = client.put(f"{API}/settings/llm", json={"provider": "openai", "model": "c"})

    assert response.status_code == 200 and response.json()["changes"]["default"]["new"] == "a/c"
    assert engine.llm.called("replace") == [(({"provider": "openai", "model": "c"},), {"by": ADMIN, "via": "api"})]


def test_replace_llm_with_a_bad_section_is_a_422(stub):
    from nl2sql import ConfigError

    client, engine = stub
    engine.llm.returns["replace"] = ConfigError(["llm.agents.ast_planer: unknown agent"])

    response = client.put(f"{API}/settings/llm", json={"provider": "openai"})

    assert response.status_code == 422 and "ast_planer" in response.json()["error"]["message"]


def test_api_key_token_returned_once(stub):
    client, engine = stub
    engine.api_keys.returns.update({"create": (KEY, TOKEN), "list": [KEY]})

    created = client.post(f"{API}/api-keys", json={"user_id": "ann", "roles": ["analyst"], "label": "ci"})

    assert created.status_code == 201, created.text
    assert created.json() == {"key": KEY.model_dump(mode="json"), "token": TOKEN}
    assert engine.api_keys.called("create") == [((), {"user_id": "ann", "roles": ["analyst"], "by": ADMIN,
                                                       "tenant_id": None, "label": "ci"})]
    listed = client.get(f"{API}/api-keys")
    assert listed.status_code == 200 and listed.json() == [KEY.model_dump(mode="json")]
    assert TOKEN not in listed.text and "token" not in listed.json()[0]


def test_creating_a_key_for_an_unknown_role_is_a_422(stub):
    client, engine = stub
    engine.api_keys.returns["create"] = ValueError("Unknown role 'root'.")

    response = client.post(f"{API}/api-keys", json={"user_id": "ann", "roles": ["root"]})

    assert response.status_code == 422 and "policies.roles" in response.json()["error"]["message"]


def test_revoking_a_key_and_an_unknown_one(stub):
    client, engine = stub

    assert client.delete(f"{API}/api-keys/ab12cd34").status_code == 204
    assert engine.api_keys.called("revoke") == [(("ab12cd34",), {"by": ADMIN})]
    engine.api_keys.returns["revoke"] = ValueError("No API key with id 'zz'.")
    assert client.delete(f"{API}/api-keys/zz").status_code == 404


def test_api_keys_are_admin_only(api_client):
    client, engine = api_client(authenticated=False, api={"auth": "static", "static_role": "analyst"})
    engine.api_keys = Recorder()

    assert client.get(f"{API}/api-keys").status_code == 403
    assert client.post(f"{API}/api-keys", json={"user_id": "a", "roles": []}).status_code == 403
    assert client.delete(f"{API}/api-keys/x").status_code == 403
    assert engine.api_keys.calls == []


def test_events_catalog_is_the_engines(stub):
    client, engine = stub
    engine.events.returns["catalog"] = [EventInfo(name="run.started", description="A run began.", run=True,
                                                  data_schema={"type": "object"})]

    body = client.get(f"{API}/events/catalog").json()

    assert body[0]["name"] == "run.started" and body[0]["data_schema"] == {"type": "object"}
