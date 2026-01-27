from nl2sql.pipeline.graph import _next_scan_layer_ids
from nl2sql.pipeline.nodes.global_planner.schemas import (
    ExecutionDAG,
    LogicalEdge,
    LogicalNode,
    RelationSchema,
    ColumnSpec,
)


def _schema(columns):
    return RelationSchema(columns=[ColumnSpec(name=c) for c in columns])


def test_layered_toposort_orders_scan_before_combine():
    scan_left = LogicalNode(
        node_id="sq_left",
        kind="scan",
        inputs=[],
        output_schema=_schema(["id"]),
    )
    scan_right = LogicalNode(
        node_id="sq_right",
        kind="scan",
        inputs=[],
        output_schema=_schema(["id"]),
    )
    combine = LogicalNode(
        node_id="combine_cg_1",
        kind="combine",
        inputs=["sq_left", "sq_right"],
        output_schema=_schema(["id"]),
    )
    edges = [
        LogicalEdge(edge_id="edge_l", from_id="sq_left", to_id="combine_cg_1"),
        LogicalEdge(edge_id="edge_r", from_id="sq_right", to_id="combine_cg_1"),
    ]
    dag = ExecutionDAG(nodes=[scan_left, scan_right, combine], edges=edges)

    assert dag.layers == [["sq_left", "sq_right"], ["combine_cg_1"]]


def test_next_scan_layer_ids_respects_results():
    scan_left = LogicalNode(
        node_id="sq_left",
        kind="scan",
        inputs=[],
        output_schema=_schema(["id"]),
    )
    scan_right = LogicalNode(
        node_id="sq_right",
        kind="scan",
        inputs=[],
        output_schema=_schema(["id"]),
    )
    combine = LogicalNode(
        node_id="combine_cg_1",
        kind="combine",
        inputs=["sq_left", "sq_right"],
        output_schema=_schema(["id"]),
    )
    edges = [
        LogicalEdge(edge_id="edge_l", from_id="sq_left", to_id="combine_cg_1"),
        LogicalEdge(edge_id="edge_r", from_id="sq_right", to_id="combine_cg_1"),
    ]
    dag = ExecutionDAG(nodes=[scan_left, scan_right, combine], edges=edges)

    assert _next_scan_layer_ids(dag, {}) == ["sq_left", "sq_right"]
    assert _next_scan_layer_ids(dag, {"sq_left": "r1"}) == ["sq_right"]
    assert _next_scan_layer_ids(dag, {"sq_left": "r1", "sq_right": "r2"}) == []
