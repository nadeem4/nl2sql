from types import SimpleNamespace

from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
from nl2sql.pipeline.state import GraphState
from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode
from nl2sql.auth import UserContext


def _doc(datasource_id: str | None = "ds1", schema_version: str = "v1"):
    doc = SimpleNamespace()
    doc.metadata = {"schema_version": schema_version}
    if datasource_id is not None:
        doc.metadata["datasource_id"] = datasource_id
    return doc


def test_datasource_resolver_schema_version_mismatch_fail(monkeypatch):
    # Validates mismatch policy because stale schema must be rejected when configured.
    # Arrange
    vector_store = SimpleNamespace()
    vector_store.retrieve_datasource_candidates = lambda *_a, **_k: [_doc()]

    rbac = SimpleNamespace(get_allowed_datasources=lambda _ctx: ["ds1"])
    ds_registry = SimpleNamespace(get_capabilities=lambda _id: {"supports_sql"})
    schema_store = SimpleNamespace(get_latest_version=lambda _id: "v2")

    ctx = SimpleNamespace(
        vector_store=vector_store,
        rbac=rbac,
        ds_registry=ds_registry,
        schema_store=schema_store,
    )
    node = DatasourceResolverNode(ctx)

    monkeypatch.setattr(
        "nl2sql.pipeline.nodes.datasource_resolver.node.settings.schema_version_mismatch_policy",
        "fail",
    )
    state = GraphState(user_query="q", user_context=UserContext())

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.INVALID_STATE


def test_datasource_resolver_handles_missing_vector_store():
    # Validates fallback behavior because resolver must fail closed without vector store.
    # Arrange
    ctx = SimpleNamespace(
        vector_store=None,
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: []),
        ds_registry=SimpleNamespace(get_capabilities=lambda _id: {"supports_sql"}),
        schema_store=SimpleNamespace(get_latest_version=lambda _id: "v1"),
    )
    node = DatasourceResolverNode(ctx)

    # Act
    result = node(GraphState(user_query="q", user_context=UserContext()))

    # Assert
    assert result["datasource_resolver_response"].resolved_datasources == []
    assert "Vector store unavailable" in result["reasoning"][0]["content"]


def test_datasource_resolver_allows_wildcard_datasource():
    # Validates wildcard because demo policy uses '*' to allow all datasources.
    vector_store = SimpleNamespace()
    vector_store.retrieve_datasource_candidates = lambda *_a, **_k: [_doc()]

    rbac = SimpleNamespace(get_allowed_datasources=lambda _ctx: ["*"])
    ds_registry = SimpleNamespace(get_capabilities=lambda _id: {"supports_sql"})
    schema_store = SimpleNamespace(get_latest_version=lambda _id: "v1")
    ctx = SimpleNamespace(
        vector_store=vector_store,
        rbac=rbac,
        ds_registry=ds_registry,
        schema_store=schema_store,
    )
    node = DatasourceResolverNode(ctx)

    result = node(GraphState(user_query="q", user_context=UserContext()))
    response = result["datasource_resolver_response"]
    assert response.allowed_datasource_ids == ["ds1"]
    assert response.resolved_datasources


def test_datasource_resolver_no_candidates_returns_error():
    # Validates error path because resolver must fail when no candidates exist.
    vector_store = SimpleNamespace(retrieve_datasource_candidates=lambda *_a, **_k: [])
    ctx = SimpleNamespace(
        vector_store=vector_store,
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: ["ds1"]),
        ds_registry=SimpleNamespace(get_capabilities=lambda _id: {"supports_sql"}),
        schema_store=SimpleNamespace(get_latest_version=lambda _id: "v1"),
    )
    node = DatasourceResolverNode(ctx)

    result = node(GraphState(user_query="q", user_context=UserContext()))

    assert result["errors"][0].error_code == ErrorCode.SCHEMA_RETRIEVAL_FAILED


def test_datasource_resolver_rbac_denies_all():
    # Validates RBAC because resolver must block unauthorized access.
    vector_store = SimpleNamespace(
        retrieve_datasource_candidates=lambda *_a, **_k: [_doc()]
    )
    ctx = SimpleNamespace(
        vector_store=vector_store,
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: []),
        ds_registry=SimpleNamespace(get_capabilities=lambda _id: {"supports_sql"}),
        schema_store=SimpleNamespace(get_latest_version=lambda _id: "v1"),
    )
    node = DatasourceResolverNode(ctx)

    result = node(GraphState(user_query="q", user_context=UserContext()))

    assert result["errors"][0].error_code == ErrorCode.SECURITY_VIOLATION


def test_datasource_resolver_unsupported_datasource():
    # Validates capability filter because unsupported datasources must be rejected.
    vector_store = SimpleNamespace(
        retrieve_datasource_candidates=lambda *_a, **_k: [_doc()]
    )
    ctx = SimpleNamespace(
        vector_store=vector_store,
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: ["ds1"]),
        ds_registry=SimpleNamespace(get_capabilities=lambda _id: set()),
        schema_store=SimpleNamespace(get_latest_version=lambda _id: "v1"),
    )
    node = DatasourceResolverNode(ctx)

    result = node(GraphState(user_query="q", user_context=UserContext()))

    assert result["errors"][0].error_code == ErrorCode.SECURITY_VIOLATION
    response = result["datasource_resolver_response"]
    assert response.unsupported_datasource_ids == ["ds1"]


def test_datasource_resolver_dedupes_candidate_docs():
    # Validates dedupe because multiple docs for same datasource should collapse.
    vector_store = SimpleNamespace(
        retrieve_datasource_candidates=lambda *_a, **_k: [_doc(), _doc()]
    )
    ctx = SimpleNamespace(
        vector_store=vector_store,
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: ["*"]),
        ds_registry=SimpleNamespace(get_capabilities=lambda _id: {"supports_sql"}),
        schema_store=SimpleNamespace(get_latest_version=lambda _id: "v1"),
    )
    node = DatasourceResolverNode(ctx)

    result = node(GraphState(user_query="q", user_context=UserContext()))

    response = result["datasource_resolver_response"]
    assert len(response.resolved_datasources) == 1


def test_datasource_resolver_missing_datasource_id():
    # Validates missing metadata because docs without datasource_id are ignored.
    vector_store = SimpleNamespace(
        retrieve_datasource_candidates=lambda *_a, **_k: [_doc(datasource_id=None)]
    )
    ctx = SimpleNamespace(
        vector_store=vector_store,
        rbac=SimpleNamespace(get_allowed_datasources=lambda _ctx: ["ds1"]),
        ds_registry=SimpleNamespace(get_capabilities=lambda _id: {"supports_sql"}),
        schema_store=SimpleNamespace(get_latest_version=lambda _id: "v1"),
    )
    node = DatasourceResolverNode(ctx)

    result = node(GraphState(user_query="q", user_context=UserContext()))

    assert result["errors"][0].error_code == ErrorCode.SCHEMA_RETRIEVAL_FAILED
