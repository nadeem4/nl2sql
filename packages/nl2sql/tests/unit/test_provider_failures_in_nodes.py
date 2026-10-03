"""Every LLM node reports a provider's refusal the same way, and the facade passes it on.

The playground showed ``The run stopped: Datasource resolution failed: Error
code: 401 - {'error': {...}}``: the node's own label, the SDK's repr and the
masked key. Each LLM node now hands a provider failure to
``nl2sql.llm.failures.provider_error``, and ``QueryResult.errors`` carries its
``provider`` and ``detail`` to every caller.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import openai
import pytest

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from nl2sql.api.query_api import result_from_state
from nl2sql.common.errors import ErrorCode
from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.pipeline.nodes.answer_synthesizer.node import AnswerSynthesizerNode
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.refiner.node import RefinerNode
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.secrets import SecretManager
from nl2sql.testing.fake_llm import FakeLLMServer

FAKE_KEY = "-".join(["sk", "proj", "nodes" + "n" * 30 + "0000"])
MASKED = "sk-proj-" + "*" * 30 + "0000"
REJECTED = {"error": {"message": f"Incorrect API key provided: {MASKED}.", "type": "invalid_request_error",
                      "param": None, "code": "invalid_api_key"}}


def _rate_limited():
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    body = {"message": "Rate limit reached.", "type": "tokens", "code": "rate_limit_exceeded"}
    response = httpx.Response(429, request=request, json={"error": body})
    return openai.RateLimitError("Error code: 429 - {...}", response=response, body=body)


def _mock_ctx():
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    return ctx


def _planner():
    node = ASTPlannerNode(_mock_ctx())
    state = SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"))
    return node, state


def _decomposer():
    node = DecomposerNode(_mock_ctx())
    state = GraphState(user_query="q", datasource_resolver_response=DatasourceResolverResponse(
        resolved_datasources=[ResolvedDatasource(datasource_id="ds1", metadata={})],
        allowed_datasource_ids=["ds1"]))
    return node, state


def _refiner():
    return RefinerNode(_mock_ctx()), SubgraphExecutionState(trace_id="t")


def _synthesizer():
    node = AnswerSynthesizerNode(_mock_ctx())
    state = GraphState(user_query="q", aggregator_response=AggregatorResponse(terminal_results={"sq1": [{"id": 1}]}))
    return node, state


@pytest.mark.parametrize("build", [_planner, _decomposer, _refiner, _synthesizer],
                         ids=["ast_planner", "decomposer", "refiner", "answer_synthesizer"])
def test_each_llm_node_reports_a_provider_failure_by_its_code(build):
    node, state = build()
    node.chain = MagicMock()
    node.chain.invoke.side_effect = _rate_limited()

    result = node(state)

    error = result["errors"][0]
    assert error.error_code == ErrorCode.PROVIDER_RATE_LIMITED
    assert error.provider == "OpenAI"
    assert "Rate limit reached." in error.detail
    assert "Error code" not in error.message
    # The reasoning log the playground shows must not repeat the raw exception.
    assert all("Error code" not in str(entry) for entry in result.get("reasoning", []))


@pytest.fixture
def rejecting_registry():
    """A real OpenAI client against a stand-in that answers every call with OpenAI's 401."""
    server = FakeLLMServer([], fail_with=(401, REJECTED)).start()
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai", model="gpt-4o", api_key=FAKE_KEY,
                                      base_url=server.base_url, name="default"))
    yield registry
    server.stop()


def _resolver_ctx(registry):
    return SimpleNamespace(
        vector_store=None,
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: ["ds1"]),
        ds_registry=SimpleNamespace(get_capabilities=lambda _id: {"supports_sql"}, list_ids=lambda: ["ds1"]),
        schema_store=SimpleNamespace(get_latest_version=lambda _id: "v1", get_latest_snapshot=lambda _id: None),
        llm_registry=registry,
    )


def test_a_rejected_key_reaches_the_facade_as_provider_auth_failed(rejecting_registry):
    from nl2sql.auth import UserContext

    node = DatasourceResolverNode(_resolver_ctx(rejecting_registry))
    state = GraphState(user_query="How many customers?", user_context=UserContext(roles=["admin"]))

    out = node(state)
    result = result_from_state({"errors": out["errors"]})

    assert result.status == "error"
    entry = result.errors[0]
    assert entry["error_code"] == "PROVIDER_AUTH_FAILED"
    assert entry["severity"] == "ERROR"
    # The stand-in listens on 127.0.0.1, which no provider preset names.
    assert entry["provider"] == "The model provider"
    assert entry["message"] == "The model provider rejected the API key."
    assert "Incorrect API key provided" in entry["detail"]
    for text in (entry["message"], entry["detail"]):
        assert "Datasource resolution failed" not in text
        assert "{'" not in text
        assert MASKED not in text and FAKE_KEY not in text and "0000" not in text


def test_an_error_with_no_provider_has_null_provider_and_detail():
    from nl2sql.common.errors import ErrorSeverity, PipelineError

    result = result_from_state({"errors": [PipelineError(node="ast_planner", message="Planner failed.",
                                                         severity=ErrorSeverity.ERROR,
                                                         error_code=ErrorCode.PLANNING_FAILURE)]})

    assert result.errors == [{"node": "ast_planner", "message": "Planner failed.",
                              "error_code": "PLANNING_FAILURE", "severity": "ERROR",
                              "provider": None, "detail": None}]
