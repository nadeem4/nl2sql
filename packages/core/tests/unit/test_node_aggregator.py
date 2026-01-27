from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.global_planner.schemas import (
    ExecutionDAG,
    LogicalNode,
    LogicalEdge,
    RelationSchema,
    ColumnSpec,
)
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.global_planner.schemas import GlobalPlannerResponse
from nl2sql_adapter_sdk.contracts import ResultFrame
from nl2sql.common.result_store import ResultStore


def _schema(columns):
    return RelationSchema(columns=[ColumnSpec(name=c) for c in columns])


def test_union_combine():
    ctx = MagicMock()
    ctx.result_store = ResultStore()
    node = EngineAggregatorNode(ctx)

    scan_left = LogicalNode(
        node_id="sq_left",
        kind="scan",
        inputs=[],
        output_schema=_schema(["id", "value"]),
    )
    scan_right = LogicalNode(
        node_id="sq_right",
        kind="scan",
        inputs=[],
        output_schema=_schema(["id", "value"]),
    )
    combine = LogicalNode(
        node_id="combine_cg_1",
        kind="combine",
        inputs=["sq_left", "sq_right"],
        output_schema=_schema(["id", "value"]),
        attributes={"operation": "union"},
    )

    dag = ExecutionDAG(
        nodes=[scan_left, scan_right, combine],
        edges=[
            LogicalEdge(edge_id="edge_l", from_id="sq_left", to_id="combine_cg_1"),
            LogicalEdge(edge_id="edge_r", from_id="sq_right", to_id="combine_cg_1"),
        ],
    )

    left_id = ctx.result_store.put(ResultFrame.from_row_dicts([{"id": 1, "value": 10}]))
    right_id = ctx.result_store.put(ResultFrame.from_row_dicts([{"id": 2, "value": 20}]))
    state = GraphState(
        user_query="union test",
        global_planner_response=GlobalPlannerResponse(execution_dag=dag),
        results={
            "sq_left": left_id,
            "sq_right": right_id,
        },
    )

    result = node(state)
    assert "combine_cg_1" in result["aggregator_response"].terminal_results
    assert len(result["aggregator_response"].terminal_results["combine_cg_1"]) == 2


def test_post_filter():
    ctx = MagicMock()
    ctx.result_store = ResultStore()
    node = EngineAggregatorNode(ctx)

    scan_left = LogicalNode(
        node_id="sq_left",
        kind="scan",
        inputs=[],
        output_schema=_schema(["id", "value"]),
    )
    combine = LogicalNode(
        node_id="combine_cg_1",
        kind="combine",
        inputs=["sq_left"],
        output_schema=_schema(["id", "value"]),
        attributes={"operation": "standalone"},
    )
    post_filter = LogicalNode(
        node_id="op_filter",
        kind="post_filter",
        inputs=["combine_cg_1"],
        output_schema=_schema(["id", "value"]),
        attributes={
            "operation": "filter",
            "filters": [{"attribute": "value", "operator": ">", "value": 15}],
        },
    )

    dag = ExecutionDAG(
        nodes=[scan_left, combine, post_filter],
        edges=[
            LogicalEdge(edge_id="edge_l", from_id="sq_left", to_id="combine_cg_1"),
            LogicalEdge(edge_id="edge_f", from_id="combine_cg_1", to_id="op_filter"),
        ],
    )

    left_id = ctx.result_store.put(
        ResultFrame.from_row_dicts([{"id": 1, "value": 10}, {"id": 2, "value": 20}])
    )
    state = GraphState(
        user_query="filter test",
        global_planner_response=GlobalPlannerResponse(execution_dag=dag),
        results={"sq_left": left_id},
    )

    result = node(state)
    assert result["aggregator_response"].terminal_results["op_filter"] == [{"id": 2, "value": 20}]
