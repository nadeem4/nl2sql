from types import SimpleNamespace
import tempfile

from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.execution.dag import (
    ExecutionDAG,
    LogicalNode,
    LogicalEdge,
)
from nl2sql.pipeline.state import GraphState
from nl2sql_adapter_sdk.contracts import ResultFrame
from nl2sql.common.errors import ErrorCode
from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig
from nl2sql.common.settings import settings




def test_aggregator_filters_rows_deterministically(monkeypatch):
    # Validates post-filter logic because aggregation must be deterministic.
    # Arrange
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[])
    post_filter = LogicalNode(
        node_id="op_filter",
        kind="post_filter",
        inputs=["sq_1"],
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
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        artifact = store.create_artifact_ref(
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
            execution_dag=dag,
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

    scan_left = LogicalNode(node_id="sq_left", kind="scan", inputs=[])
    scan_right = LogicalNode(node_id="sq_right", kind="scan", inputs=[])
    combine = LogicalNode(
        node_id="combine_cg_1",
        kind="combine",
        inputs=["sq_left", "sq_right"],
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
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        left_artifact = store.create_artifact_ref(
            ResultFrame.from_row_dicts([{"id": 1}]),
            {
                "tenant_id": "t1",
                "request_id": "r1_left",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_left",
                "schema_version": "v1",
            },
        )
        right_artifact = store.create_artifact_ref(
            ResultFrame.from_row_dicts([{"id": 1}]),
            {
                "tenant_id": "t1",
                "request_id": "r1_right",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_right",
                "schema_version": "v1",
            },
        )
        state = GraphState(
            user_query="q",
            execution_dag=dag,
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
    state = GraphState(user_query="q", execution_dag=None)

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.AGGREGATOR_FAILED


def test_aggregator_post_aggregate_sum(monkeypatch):
    # Validates post-aggregate because aggregations must be computed deterministically.
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[])
    post_agg = LogicalNode(
        node_id="op_agg",
        kind="post_aggregate",
        inputs=["sq_1"],
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
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        artifact = store.create_artifact_ref(
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
            execution_dag=dag,
            artifact_refs={"sq_1": artifact},
        )

        response = node(state)["aggregator_response"]

        rows = response.terminal_results["op_agg"]
        assert rows[0]["value"] == 12


def test_aggregator_post_sort_limit(monkeypatch):
    # Validates sort/limit because ordering and truncation must be enforced.
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[])
    post_sort = LogicalNode(
        node_id="op_sort",
        kind="post_sort",
        inputs=["sq_1"],
        attributes={
            "operation": "sort",
            "order_by": [{"attribute": "value", "direction": "desc"}],
        },
    )
    post_limit = LogicalNode(
        node_id="op_limit",
        kind="post_limit",
        inputs=["op_sort"],
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
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        artifact = store.create_artifact_ref(
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
            execution_dag=dag,
            artifact_refs={"sq_1": artifact},
        )

        response = node(state)["aggregator_response"]

        rows = response.terminal_results["op_limit"]
        assert rows == [{"value": 3}]


def test_aggregator_union_combines_rows(monkeypatch):
    # Validates union because combined result should include all rows.
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan_left = LogicalNode(node_id="sq_left", kind="scan", inputs=[])
    scan_right = LogicalNode(node_id="sq_right", kind="scan", inputs=[])
    combine = LogicalNode(
        node_id="combine_union",
        kind="combine",
        inputs=["sq_left", "sq_right"],
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
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        left_artifact = store.create_artifact_ref(
            ResultFrame.from_row_dicts([{"id": 1}]),
            {
                "tenant_id": "t1",
                "request_id": "r1_left",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_left",
                "schema_version": "v1",
            },
        )
        right_artifact = store.create_artifact_ref(
            ResultFrame.from_row_dicts([{"id": 2}]),
            {
                "tenant_id": "t1",
                "request_id": "r1_right",
                "subgraph_name": "sql_agent",
                "dag_node_id": "sq_right",
                "schema_version": "v1",
            },
        )
        state = GraphState(
            user_query="q",
            execution_dag=dag,
            artifact_refs={"sq_left": left_artifact, "sq_right": right_artifact},
        )

        response = node(state)["aggregator_response"]

        rows = response.terminal_results["combine_union"]
        assert sorted([r["id"] for r in rows]) == [1, 2]


def test_aggregator_missing_artifact_reference():
    # Validates missing artifact handling because inputs may be incomplete.
    ctx = SimpleNamespace()
    node = EngineAggregatorNode(ctx)

    scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[])
    dag = ExecutionDAG(nodes=[scan], edges=[])
    state = GraphState(user_query="q", execution_dag=dag)

    result = node(state)

    assert result["errors"][0].error_code == ErrorCode.AGGREGATOR_FAILED


def test_the_jazz_but_never_rock_dag_is_refused_with_the_reason(monkeypatch):
    # chinook_009, the shape recorded in
    # benchmarks/tier2/chinook/2026-09-23_de42c40_gpt-5.4.json: two sub-queries
    # each selecting one column aliased `customer`, joined, then a post-combine
    # op reaching for `right.customer` to negate against. It has failed in all
    # three tier 2 runs; what reached the caller was polars' own `unable to
    # find column "right.customer"; valid columns: ["customer"]`.
    # Arrange
    node = EngineAggregatorNode(SimpleNamespace())

    jazz = LogicalNode(node_id="sq_jazz", kind="scan", inputs=[])
    rock = LogicalNode(node_id="sq_rock", kind="scan", inputs=[])
    combine = LogicalNode(
        node_id="combine_cg_1",
        kind="combine",
        inputs=["sq_jazz", "sq_rock"],
        attributes={"operation": "join",
                    "join_keys": [{"left": "customer", "right": "right.customer"}]},
    )
    post_filter = LogicalNode(
        node_id="op_not_rock",
        kind="post_filter",
        inputs=["combine_cg_1"],
        attributes={"operation": "filter",
                    "filters": [{"attribute": "right.customer", "operator": "=", "value": None}]},
    )
    dag = ExecutionDAG(
        nodes=[jazz, rock, combine, post_filter],
        edges=[
            LogicalEdge(edge_id="e_j", from_id="sq_jazz", to_id="combine_cg_1", role="left"),
            LogicalEdge(edge_id="e_r", from_id="sq_rock", to_id="combine_cg_1", role="right"),
            LogicalEdge(edge_id="e_f", from_id="combine_cg_1", to_id="op_not_rock"),
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
        store = ArtifactStore(
            ArtifactStoreConfig(
                backend="local",
                base_uri=tmpdir,
                path_template=settings.result_artifact_path_template,
            )
        )
        refs = {}
        for node_id, names in (("sq_jazz", ["Ann", "Bo"]), ("sq_rock", ["Bo", "Cy"])):
            refs[node_id] = store.create_artifact_ref(
                ResultFrame.from_row_dicts([{"customer": n} for n in names]),
                {"tenant_id": "t1", "request_id": f"r1_{node_id}", "subgraph_name": "sql_agent",
                 "dag_node_id": node_id, "schema_version": "v1"},
            )
        state = GraphState(
            user_query="Which customers bought jazz tracks but never rock?",
            execution_dag=dag,
            artifact_refs=refs,
        )

        # Act
        result = node(state)

    # Assert
    [error] = result["errors"]
    assert error.error_code == ErrorCode.AGGREGATOR_FAILED
    assert "unable to find column" not in error.message  # polars' words, not ours
    assert "anti-join" in error.message
    assert "cannot be expressed" in error.message
    assert "right.customer" in error.message
