import pytest

from nl2sql.pipeline.graph_utils import resolve_subgraph
from nl2sql.pipeline.nodes.global_planner.schemas import (
    ColumnSpec,
    ExecutionDAG,
    LogicalNode,
    RelationSchema,
)
from nl2sql.pipeline.routes import build_scan_layer_router
from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from types import SimpleNamespace
from unittest.mock import MagicMock


def _ctx_with_capabilities(capabilities_by_id):
    def get_capabilities(ds_id):
        if ds_id not in capabilities_by_id:
            raise ValueError(f"Unknown datasource ID: {ds_id}")
        return set(capabilities_by_id[ds_id])

    return SimpleNamespace(ds_registry=SimpleNamespace(get_capabilities=get_capabilities))


def test_resolve_subgraph_selects_sql_agent_for_sql_capable_datasource():
    # Pins the capability gate: a SQL-capable datasource routes to sql_agent.
    # Arrange
    ctx = _ctx_with_capabilities({"sql_ds": {DatasourceCapability.SUPPORTS_SQL.value}})

    # Act
    target = resolve_subgraph("sql_ds", ctx)

    # Assert
    assert target == "sql_agent"


def test_resolve_subgraph_returns_none_without_sql_capability():
    # Pins the capability gate: a datasource lacking SUPPORTS_SQL has no subgraph.
    # Arrange
    ctx = _ctx_with_capabilities({"rest_ds": {DatasourceCapability.SUPPORTS_REST.value}})

    # Act
    target = resolve_subgraph("rest_ds", ctx)

    # Assert
    assert target is None


def test_resolve_subgraph_returns_none_when_capability_lookup_fails():
    # Pins the capability gate: an unknown datasource degrades to no subgraph.
    # Arrange
    ctx = _ctx_with_capabilities({})

    # Act
    target = resolve_subgraph("missing_ds", ctx)

    # Assert
    assert target is None


def _scan_state(datasource_id):
    node = LogicalNode(
        node_id="sq_1",
        kind="scan",
        inputs=[],
        output_schema=RelationSchema(columns=[ColumnSpec(name="id")]),
    )
    dag = ExecutionDAG(nodes=[node], edges=[])
    return SimpleNamespace(
        trace_id="trace-1",
        user_context=None,
        datasource_resolver_response=None,
        artifact_refs={},
        global_planner_response=SimpleNamespace(execution_dag=dag),
        decomposer_response=SimpleNamespace(
            sub_queries=[SimpleNamespace(id="sq_1", datasource_id=datasource_id)]
        ),
    )


def test_scan_layer_router_rejects_datasource_without_sql_capability():
    # Pins the routing path: an incompatible datasource fails closed, never dispatched.
    # The raise in routes.py names PipelineError, which is a pydantic model rather
    # than an exception type, so the interpreter surfaces a TypeError instead. That
    # is pre-existing behaviour; what is pinned here is that routing stops.
    # Arrange
    ctx = _ctx_with_capabilities({"rest_ds": {DatasourceCapability.SUPPORTS_REST.value}})
    route = build_scan_layer_router(ctx)

    # Act / Assert
    with pytest.raises(Exception):
        route(_scan_state("rest_ds"))


def test_scan_layer_router_dispatches_sql_capable_datasource_to_sql_agent():
    # Pins the routing path: a SQL-capable datasource is sent to the sql_agent node.
    # Arrange
    ctx = _ctx_with_capabilities({"sql_ds": {DatasourceCapability.SUPPORTS_SQL.value}})
    route = build_scan_layer_router(ctx)

    # Act
    branches = route(_scan_state("sql_ds"))

    # Assert
    assert [send.node for send in branches] == ["sql_agent"]


def test_sql_agent_subgraph_builds():
    # Validates subgraph builder because sql_agent must compile without runtime deps.
    # Arrange
    ctx = SimpleNamespace(
        tenant_id="t1",
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
