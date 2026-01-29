from types import SimpleNamespace
import tempfile

from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.global_planner.schemas import (
    ExecutionDAG,
    LogicalNode,
    LogicalEdge,
    RelationSchema,
    ColumnSpec,
)
from nl2sql.pipeline.nodes.global_planner.schemas import GlobalPlannerResponse
from nl2sql.pipeline.state import GraphState
from nl2sql_adapter_sdk.contracts import ResultFrame
from nl2sql.common.errors import ErrorCode
from nl2sql.execution.artifacts.base import ArtifactStoreConfig
from nl2sql.execution.artifacts.local_store import LocalArtifactStore
from nl2sql.common.settings import settings


def _schema(columns):
    return RelationSchema(columns=[ColumnSpec(name=c) for c in columns])


def test_aggregator_filters_rows_deterministically(monkeypatch):
    # Validates post-filter logic because aggregation must be deterministic.
    # Arrange
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[], output_schema=_schema(["id", "value"]))
    post_filter = LogicalNode(
        node_id="op_filter",
        kind="post_filter",
        inputs=["sq_1"],
        output_schema=_schema(["id", "value"]),
        attributes={"operation": "filter", "filters": [{"attribute": "value", "operator": ">", "value": 10}]},
    )
    dag = ExecutionDAG(
        nodes=[scan, post_filter],
        edges=[LogicalEdge(edge_id="edge_f", from_id="sq_1", to_id="op_filter")],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(settings, "result_artifact_backend", "local")
        monkeypatch.setattr(settings, "result_artifact_base_uri", tmpdir)
        monkeypatch.setattr(
            settings,
            "result_artifact_path_template",
            "<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet",
        )
        store = LocalArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        artifact = store.write_result_frame(
            ResultFrame.from_row_dicts([{"id": 1, "value": 5}, {"id": 2, "value": 20}]),
            {
                "tenant_id": "t1",
                "request_id": "r1",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_1",
                "schema_version": "v1",
            },
        )
        state = GraphState(
            user_query="q",
            global_planner_response=GlobalPlannerResponse(execution_dag=dag),
            artifact_refs={"sq_1": artifact},
        )

        # Act
        response = node(state)["aggregator_response"]

        # Assert
        rows = response.terminal_results["op_filter"]
        assert [r["id"] for r in rows] == [2]


def test_aggregator_join_requires_keys(monkeypatch):
    # Validates join guardrails because joins without keys must fail fast.
    # Arrange
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan_left = LogicalNode(node_id="sq_left", kind="scan", inputs=[], output_schema=_schema(["id"]))
    scan_right = LogicalNode(node_id="sq_right", kind="scan", inputs=[], output_schema=_schema(["id"]))
    combine = LogicalNode(
        node_id="combine_cg_1",
        kind="combine",
        inputs=["sq_left", "sq_right"],
        output_schema=_schema(["id"]),
        attributes={"operation": "join", "join_keys": []},
    )
    dag = ExecutionDAG(
        nodes=[scan_left, scan_right, combine],
        edges=[
            LogicalEdge(edge_id="edge_l", from_id="sq_left", to_id="combine_cg_1"),
            LogicalEdge(edge_id="edge_r", from_id="sq_right", to_id="combine_cg_1"),
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(settings, "result_artifact_backend", "local")
        monkeypatch.setattr(settings, "result_artifact_base_uri", tmpdir)
        monkeypatch.setattr(
            settings,
            "result_artifact_path_template",
            "<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet",
        )
        store = LocalArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        left_artifact = store.write_result_frame(
            ResultFrame.from_row_dicts([{"id": 1}]),
            {
                "tenant_id": "t1",
                "request_id": "r1",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_left",
                "schema_version": "v1",
            },
        )
        right_artifact = store.write_result_frame(
            ResultFrame.from_row_dicts([{"id": 1}]),
            {
                "tenant_id": "t1",
                "request_id": "r1",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_right",
                "schema_version": "v1",
            },
        )
        state = GraphState(
            user_query="q",
            global_planner_response=GlobalPlannerResponse(execution_dag=dag),
            artifact_refs={"sq_left": left_artifact, "sq_right": right_artifact},
        )

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.AGGREGATOR_FAILED


def test_aggregator_requires_execution_dag():
    # Validates DAG requirement because aggregation must fail without a plan.
    # Arrange
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)
    state = GraphState(user_query="q", global_planner_response=None)

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.AGGREGATOR_FAILED


def test_aggregator_post_aggregate_sum(monkeypatch):
    # Validates post-aggregate because aggregations must be computed deterministically.
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[], output_schema=_schema(["value"]))
    post_agg = LogicalNode(
        node_id="op_agg",
        kind="post_aggregate",
        inputs=["sq_1"],
        output_schema=_schema(["total_value"]),
        attributes={
            "operation": "aggregate",
            "metrics": [{"name": "value", "aggregation": "sum"}],
            "group_by": [],
        },
    )
    dag = ExecutionDAG(
        nodes=[scan, post_agg],
        edges=[LogicalEdge(edge_id="edge_a", from_id="sq_1", to_id="op_agg")],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(settings, "result_artifact_backend", "local")
        monkeypatch.setattr(settings, "result_artifact_base_uri", tmpdir)
        monkeypatch.setattr(
            settings,
            "result_artifact_path_template",
            "<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet",
        )
        store = LocalArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        artifact = store.write_result_frame(
            ResultFrame.from_row_dicts([{"value": 5}, {"value": 7}]),
            {
                "tenant_id": "t1",
                "request_id": "r1",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_1",
                "schema_version": "v1",
            },
        )
        state = GraphState(
            user_query="q",
            global_planner_response=GlobalPlannerResponse(execution_dag=dag),
            artifact_refs={"sq_1": artifact},
        )

        response = node(state)["aggregator_response"]

        rows = response.terminal_results["op_agg"]
        assert rows[0]["value"] == 12


def test_aggregator_post_sort_limit(monkeypatch):
    # Validates sort/limit because ordering and truncation must be enforced.
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[], output_schema=_schema(["value"]))
    post_sort = LogicalNode(
        node_id="op_sort",
        kind="post_sort",
        inputs=["sq_1"],
        output_schema=_schema(["value"]),
        attributes={
            "operation": "sort",
            "order_by": [{"attribute": "value", "direction": "desc"}],
        },
    )
    post_limit = LogicalNode(
        node_id="op_limit",
        kind="post_limit",
        inputs=["op_sort"],
        output_schema=_schema(["value"]),
        attributes={
            "operation": "limit",
            "limit": 1,
        },
    )
    dag = ExecutionDAG(
        nodes=[scan, post_sort, post_limit],
        edges=[
            LogicalEdge(edge_id="edge_s", from_id="sq_1", to_id="op_sort"),
            LogicalEdge(edge_id="edge_l", from_id="op_sort", to_id="op_limit"),
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(settings, "result_artifact_backend", "local")
        monkeypatch.setattr(settings, "result_artifact_base_uri", tmpdir)
        monkeypatch.setattr(
            settings,
            "result_artifact_path_template",
            "<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet",
        )
        store = LocalArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        artifact = store.write_result_frame(
            ResultFrame.from_row_dicts([{"value": 1}, {"value": 3}, {"value": 2}]),
            {
                "tenant_id": "t1",
                "request_id": "r1",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_1",
                "schema_version": "v1",
            },
        )
        state = GraphState(
            user_query="q",
            global_planner_response=GlobalPlannerResponse(execution_dag=dag),
            artifact_refs={"sq_1": artifact},
        )

        response = node(state)["aggregator_response"]

        rows = response.terminal_results["op_limit"]
        assert rows == [{"value": 3}]


def test_aggregator_union_combines_rows(monkeypatch):
    # Validates union because combined result should include all rows.
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan_left = LogicalNode(node_id="sq_left", kind="scan", inputs=[], output_schema=_schema(["id"]))
    scan_right = LogicalNode(node_id="sq_right", kind="scan", inputs=[], output_schema=_schema(["id"]))
    combine = LogicalNode(
        node_id="combine_union",
        kind="combine",
        inputs=["sq_left", "sq_right"],
        output_schema=_schema(["id"]),
        attributes={"operation": "union", "join_keys": []},
    )
    dag = ExecutionDAG(
        nodes=[scan_left, scan_right, combine],
        edges=[
            LogicalEdge(edge_id="edge_l", from_id="sq_left", to_id="combine_union"),
            LogicalEdge(edge_id="edge_r", from_id="sq_right", to_id="combine_union"),
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(settings, "result_artifact_backend", "local")
        monkeypatch.setattr(settings, "result_artifact_base_uri", tmpdir)
        monkeypatch.setattr(
            settings,
            "result_artifact_path_template",
            "<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet",
        )
        store = LocalArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        left_artifact = store.write_result_frame(
            ResultFrame.from_row_dicts([{"id": 1}]),
            {
                "tenant_id": "t1",
                "request_id": "r1",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_left",
                "schema_version": "v1",
            },
        )
        right_artifact = store.write_result_frame(
            ResultFrame.from_row_dicts([{"id": 2}]),
            {
                "tenant_id": "t1",
                "request_id": "r1",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_right",
                "schema_version": "v1",
            },
        )
        state = GraphState(
            user_query="q",
            global_planner_response=GlobalPlannerResponse(execution_dag=dag),
            artifact_refs={"sq_left": left_artifact, "sq_right": right_artifact},
        )

        response = node(state)["aggregator_response"]

        rows = response.terminal_results["combine_union"]
        assert sorted([r["id"] for r in rows]) == [1, 2]


def test_aggregator_missing_artifact_reference():
    # Validates missing artifact handling because inputs may be incomplete.
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[], output_schema=_schema(["id"]))
    dag = ExecutionDAG(nodes=[scan], edges=[])
    state = GraphState(user_query="q", global_planner_response=GlobalPlannerResponse(execution_dag=dag))

    result = node(state)

    assert result["errors"][0].error_code == ErrorCode.AGGREGATOR_FAILED
