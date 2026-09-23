from nl2sql.pipeline.graph_utils import next_scan_layer_ids
from nl2sql.execution.dag import (
    ExecutionDAG,
    LogicalNode,
    LogicalEdge,
)




def test_next_scan_layer_ids_respects_existing_results():
    # Validates DAG routing because scan layers must honor completed nodes.
    # Arrange
    scan_left = LogicalNode(node_id="sq_left", kind="scan", inputs=[])
    scan_right = LogicalNode(node_id="sq_right", kind="scan", inputs=[])
    combine = LogicalNode(
        node_id="combine_cg_1",
        kind="combine",
        inputs=["sq_left", "sq_right"],
    )
    dag = ExecutionDAG(
        nodes=[scan_left, scan_right, combine],
        edges=[
            LogicalEdge(edge_id="edge_l", from_id="sq_left", to_id="combine_cg_1"),
            LogicalEdge(edge_id="edge_r", from_id="sq_right", to_id="combine_cg_1"),
        ],
    )

    # Act / Assert
    assert next_scan_layer_ids(dag, {}) == ["sq_left", "sq_right"]
    assert next_scan_layer_ids(dag, {"sq_left": "r1"}) == ["sq_right"]
    assert next_scan_layer_ids(dag, {"sq_left": "r1", "sq_right": "r2"}) == []


def test_completed_scan_without_artifact_is_not_pending():
    # A scan that already ran and failed has no artifact; it must not be
    # re-dispatched forever.
    dag = ExecutionDAG(
        nodes=[
            LogicalNode(
                node_id="sq1",
                kind="scan",
                attributes={},
                inputs=[],
            )
        ],
        edges=[],
        layers=[["sq1"]],
    )

    assert next_scan_layer_ids(dag, {}, completed_node_ids=frozenset({"sq1"})) == []
    assert next_scan_layer_ids(dag, {}) == ["sq1"]
