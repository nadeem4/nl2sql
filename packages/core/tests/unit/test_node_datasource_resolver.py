from types import SimpleNamespace

from nl2sql.pipeline.nodes.datasource_resolver.node import DatasourceResolverNode
from nl2sql.pipeline.state import GraphState
from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode
from nl2sql.auth import UserContext


def test_datasource_resolver_schema_version_mismatch_fail(monkeypatch):
    # Validates mismatch policy because stale schema must be rejected when configured.
    # Arrange
    vector_store = SimpleNamespace()
    doc = SimpleNamespace()
    doc.metadata = {"datasource_id": "ds1", "schema_version": "v1"}
    vector_store.retrieve_datasource_candidates = lambda *_a, **_k: [doc]

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
