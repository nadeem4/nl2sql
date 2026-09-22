from types import SimpleNamespace

import pytest

from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
from nl2sql.pipeline.state import GraphState
from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode
from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.secrets import SecretManager
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

FAKE_KEY = "-".join(["fake", "key", "for", "tests"])

# Two datasources, so the resolver runs its vector search. With one it skips
# the search (see test_resolver_answerability.py).
TWO = ["ds1", "ds2"]


@pytest.fixture(scope="module")
def llm_registry():
    """Answers the resolver's answerability check with 'ds1 can answer'."""
    server = FakeLLMServer([Rule("AnswerabilityResponse",
                                 {"answerable_datasource_ids": ["ds1"], "reason": "ok"})]).start()
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai", model="gpt-4o", api_key=FAKE_KEY,
                                      base_url=server.base_url, name="default"))
    yield registry
    server.stop()


def _doc(datasource_id: str | None = "ds1", schema_version: str = "v1"):
    doc = SimpleNamespace()
    doc.metadata = {"schema_version": schema_version}
    if datasource_id is not None:
        doc.metadata["datasource_id"] = datasource_id
    return doc


def _schema_store(version="v1"):
    return SimpleNamespace(get_latest_version=lambda _id: version, get_latest_snapshot=lambda _id: None)


def _ctx(llm_registry, vector_store, allowed, ds_ids=TWO, version="v1"):
    return SimpleNamespace(
        vector_store=vector_store,
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: allowed),
        ds_registry=SimpleNamespace(
            get_capabilities=lambda _id: {"supports_sql"},
            list_ids=lambda: list(ds_ids),
        ),
        schema_store=_schema_store(version),
        llm_registry=llm_registry,
    )


def test_datasource_resolver_schema_version_mismatch_fail(monkeypatch, llm_registry):
    # Validates mismatch policy because stale schema must be rejected when configured.
    # Arrange
    vector_store = SimpleNamespace()
    vector_store.retrieve_datasource_candidates = lambda *_a, **_k: [_doc()]
    node = DatasourceResolverNode(_ctx(llm_registry, vector_store, ["ds1"], version="v2"))

    monkeypatch.setattr(
        "nl2sql.pipeline.nodes.datasource_resolver.node.settings.schema_version_mismatch_policy",
        "fail",
    )
    state = GraphState(user_query="q", user_context=UserContext())

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.INVALID_STATE


def test_datasource_resolver_handles_missing_vector_store(llm_registry):
    # With more than one datasource the resolver needs its vector search, so a
    # missing store is an error rather than a silent end of the run.
    node = DatasourceResolverNode(_ctx(llm_registry, None, []))

    result = node(GraphState(user_query="q", user_context=UserContext()))

    assert result["datasource_resolver_response"].resolved_datasources == []
    assert result["errors"][0].error_code == ErrorCode.SCHEMA_RETRIEVAL_FAILED
    assert "Vector store unavailable" in result["errors"][0].message


def test_datasource_resolver_allows_wildcard_datasource(llm_registry):
    # Validates wildcard because demo policy uses '*' to allow all datasources.
    vector_store = SimpleNamespace(retrieve_datasource_candidates=lambda *_a, **_k: [_doc()])
    node = DatasourceResolverNode(_ctx(llm_registry, vector_store, ["*"]))

    result = node(GraphState(user_query="q", user_context=UserContext()))
    response = result["datasource_resolver_response"]
    assert response.allowed_datasource_ids == ["ds1"]
    assert response.resolved_datasources


def test_datasource_resolver_no_candidates_returns_error(llm_registry):
    # Validates error path because resolver must fail when no candidates exist.
    vector_store = SimpleNamespace(retrieve_datasource_candidates=lambda *_a, **_k: [])
    node = DatasourceResolverNode(_ctx(llm_registry, vector_store, ["ds1"]))

    result = node(GraphState(user_query="q", user_context=UserContext()))

    assert result["errors"][0].error_code == ErrorCode.SCHEMA_RETRIEVAL_FAILED


def test_datasource_resolver_rbac_denies_all(llm_registry):
    # Validates RBAC because resolver must block unauthorized access.
    vector_store = SimpleNamespace(retrieve_datasource_candidates=lambda *_a, **_k: [_doc()])
    node = DatasourceResolverNode(_ctx(llm_registry, vector_store, []))

    result = node(GraphState(user_query="q", user_context=UserContext()))

    assert result["errors"][0].error_code == ErrorCode.SECURITY_VIOLATION


def test_datasource_resolver_unsupported_datasource(llm_registry):
    # Validates the registry filter because unsupported datasources must be rejected.
    vector_store = SimpleNamespace(retrieve_datasource_candidates=lambda *_a, **_k: [_doc()])
    # "ds1" is not registered, so the registry does not list it.
    node = DatasourceResolverNode(_ctx(llm_registry, vector_store, ["ds1"], ds_ids=[]))

    result = node(
        GraphState(user_query="q", user_context=UserContext(), datasource_id="ds1")
    )

    assert result["errors"][0].error_code == ErrorCode.INVALID_STATE
    response = result["datasource_resolver_response"]
    assert response.unsupported_datasource_ids == ["ds1"]


def test_datasource_resolver_dedupes_candidate_docs(llm_registry):
    # Validates dedupe because multiple docs for same datasource should collapse.
    vector_store = SimpleNamespace(retrieve_datasource_candidates=lambda *_a, **_k: [_doc(), _doc()])
    node = DatasourceResolverNode(_ctx(llm_registry, vector_store, ["*"]))

    result = node(GraphState(user_query="q", user_context=UserContext()))

    response = result["datasource_resolver_response"]
    assert len(response.resolved_datasources) == 1


def test_datasource_resolver_missing_datasource_id(llm_registry):
    # Validates missing metadata because docs without datasource_id are ignored.
    vector_store = SimpleNamespace(
        retrieve_datasource_candidates=lambda *_a, **_k: [_doc(datasource_id=None)]
    )
    node = DatasourceResolverNode(_ctx(llm_registry, vector_store, ["ds1"]))

    result = node(GraphState(user_query="q", user_context=UserContext()))

    assert result["errors"][0].error_code == ErrorCode.SCHEMA_RETRIEVAL_FAILED


def test_datasource_resolver_explicit_datasource_override(llm_registry):
    # Validates the explicit override path because --ds-id / API datasource_id must
    # resolve a registered, allowed datasource and populate schema_version.
    vector_store = SimpleNamespace(retrieve_datasource_candidates=lambda *_a, **_k: [])
    node = DatasourceResolverNode(_ctx(llm_registry, vector_store, ["ds1"], version="v7"))

    result = node(
        GraphState(user_query="q", user_context=UserContext(), datasource_id="ds1")
    )

    assert "errors" not in result
    response = result["datasource_resolver_response"]
    assert response.allowed_datasource_ids == ["ds1"]
    assert len(response.resolved_datasources) == 1
    resolved = response.resolved_datasources[0]
    assert resolved.datasource_id == "ds1"
    assert resolved.schema_version == "v7"


def test_resolved_metadata_has_a_stable_key_order(llm_registry):
    """The vector store hands metadata back in no fixed key order, and the
    decomposer prints it into its prompt. Two runs of the same question then sent
    different prompts, which a trace replay reports as a divergence."""
    doc = SimpleNamespace(metadata={"type": "schema.datasource", "schema_version": "v1",
                                    "id": "schema.datasource:ds1:v1", "datasource_id": "ds1"})
    vector_store = SimpleNamespace(retrieve_datasource_candidates=lambda *_a, **_k: [doc])
    ctx = _ctx(llm_registry, vector_store, ["ds1"])
    result = DatasourceResolverNode(ctx)(GraphState(user_query="q", user_context=UserContext()))
    [resolved] = result["datasource_resolver_response"].resolved_datasources
    assert list(resolved.metadata) == ["datasource_id", "id", "schema_version", "type"]


def test_an_empty_index_gets_an_actionable_error(llm_registry):
    # The owner's demo: 0 entries, and every question said only
    # "No datasource candidates resolved." with nothing to do about it.
    vector_store = SimpleNamespace(
        retrieve_datasource_candidates=lambda *_a, **_k: [],
        is_empty=lambda: True,
    )

    result = DatasourceResolverNode(_ctx(llm_registry, vector_store, ["*"]))(
        GraphState(user_query="q", user_context=UserContext())
    )

    error = result["errors"][0]
    assert error.error_code == ErrorCode.SCHEMA_RETRIEVAL_FAILED
    assert "vector index is empty" in error.message
    assert "nl2sql index" in error.message


def test_no_match_in_a_populated_index_keeps_the_original_message(llm_registry):
    vector_store = SimpleNamespace(
        retrieve_datasource_candidates=lambda *_a, **_k: [],
        is_empty=lambda: False,
    )

    result = DatasourceResolverNode(_ctx(llm_registry, vector_store, ["*"]))(
        GraphState(user_query="q", user_context=UserContext())
    )

    assert result["errors"][0].message == "No datasource candidates resolved."
