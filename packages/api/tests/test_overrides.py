"""Request headers become the engine's ``LLMOverride`` (docs/architecture/wave_d.md, D210)."""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from nl2sql import LLMOverride, MissingProviderKey
from nl2sql_api import errors
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import RequestRefused
from nl2sql_api.overrides import llm_override, parse_override

KEY = "sk-secret-value-1234567890"
MODELS = "X-NL2SQL-Models"


def test_overrides_headers_to_llm_override():
    override = parse_override({"X-NL2SQL-Api-Key-anthropic": KEY, "x-nl2sql-api-key-OpenAI": "sk-two",
                               MODELS: '{"ast_planner": "anthropic:claude-opus-5"}', "Accept": "*/*"},
                              request_llm=True)
    assert override == LLMOverride(keys={"anthropic": KEY, "openai": "sk-two"},
                                   models={"ast_planner": ("anthropic", "claude-opus-5")})
    assert KEY not in repr(override)


def test_no_headers_is_no_override():
    assert parse_override({"Accept": "*/*"}, request_llm=False) is None
    assert parse_override({"Accept": "*/*"}, request_llm=True) is None


def test_keys_without_models_run_the_configured_models():
    assert parse_override({"X-NL2SQL-Api-Key-openai": KEY}, request_llm=True) == LLMOverride(keys={"openai": KEY})


def test_headers_are_refused_when_request_llm_is_off():
    with pytest.raises(RequestRefused) as refused:
        parse_override({"X-NL2SQL-Api-Key-openai": KEY}, request_llm=False)
    assert refused.value.status == 403 and "api.request_llm" in refused.value.message
    assert KEY not in refused.value.message


def test_a_model_on_a_provider_with_no_request_key_is_refused():
    headers = {"X-NL2SQL-Api-Key-openai": KEY, MODELS: '{"ast_planner": "anthropic:claude-opus-5"}'}
    with pytest.raises(MissingProviderKey) as refused:
        parse_override(headers, request_llm=True)
    assert (refused.value.agent, refused.value.provider, refused.value.status) == ("ast_planner", "anthropic", 422)


@pytest.mark.parametrize("value", ["not json", "[1]", '{"ast_planner": 3}', '{"ast_planner": "no-colon"}'])
def test_a_malformed_models_header_is_422(value):
    with pytest.raises(RequestRefused) as refused:
        parse_override({MODELS: value}, request_llm=True)
    assert refused.value.status == 422 and refused.value.code == "INVALID_REQUEST"


def test_the_dependency_reads_request_llm_from_the_engine():
    class Engine:
        class config:
            class api:
                request_llm = False

    app = FastAPI()
    errors.install(app)

    @app.get("/probe")
    def probe(override=Depends(llm_override)):
        return {"override": override is not None}

    app.dependency_overrides[get_engine] = lambda: Engine
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/probe").json() == {"override": False}
    refused = client.get("/probe", headers={"X-NL2SQL-Api-Key-openai": KEY})
    assert refused.status_code == 403 and KEY not in refused.text
    Engine.config.api.request_llm = True
    assert client.get("/probe", headers={"X-NL2SQL-Api-Key-openai": KEY}).json() == {"override": True}
