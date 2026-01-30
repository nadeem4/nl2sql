from nl2sql.pipeline.subgraphs.registry import build_subgraph_registry
from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_subgraph_registry_includes_sql_agent():
    # Validates registry contracts because routing depends on declared capabilities.
    # Arrange
    registry = build_subgraph_registry(ctx=None)

    # Act
    spec = registry["sql_agent"]

    # Assert
    assert DatasourceCapability.SUPPORTS_SQL.value in spec.required_capabilities
    assert spec.builder is not None


def test_sql_agent_subgraph_builds():
    # Validates subgraph builder because sql_agent must compile without runtime deps.
    # Arrange
    ctx = SimpleNamespace(
        llm_registry=SimpleNamespace(get_llm=lambda _name: MagicMock()),
        vector_store=SimpleNamespace(),
        schema_store=SimpleNamespace(),
        rbac=SimpleNamespace(get_allowed_tables=lambda _ctx: ["*"]),
        ds_registry=SimpleNamespace(get_adapter=lambda _id: SimpleNamespace(get_dialect=lambda: "sqlite")),
    )

    # Act
    graph = build_sql_agent_graph(ctx)

    # Assert
    assert hasattr(graph, "invoke")
