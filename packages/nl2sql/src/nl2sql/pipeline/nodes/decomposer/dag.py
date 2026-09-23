"""The execution DAG, built from the decomposition it is a pure function of.

The decomposer says which sub-queries answer the question and how their results
combine. Turning that into the graph the layer router walks and the aggregation
service runs is a format conversion, not a decision: it reads nothing but the
:class:`~nl2sql.pipeline.nodes.decomposer.schemas.DecomposerResponse`. It used
to be a graph node of its own (``global_planner``), which bought a node, a
response model and a state field for a function call.

Node ids are the decomposition's own ids -- a scan node *is* its sub-query, by
id -- so nothing has to map between the two. Nodes and edges are sorted before
the DAG is built, so the same decomposition always gives the same layers.
"""
from __future__ import annotations

from typing import Dict, List

from nl2sql.execution.dag import ExecutionDAG, LogicalEdge, LogicalNode

from .schemas import DecomposerResponse

# The logical node kind each post-combine operation becomes.
_POST_OP_KINDS = {
    "filter": "post_filter",
    "aggregate": "post_aggregate",
    "project": "post_project",
    "sort": "post_sort",
    "limit": "post_limit",
}


def build_execution_dag(response: DecomposerResponse) -> ExecutionDAG:
    """The execution DAG for a decomposition.

    Args:
        response: The decomposer's own output. ``DecomposerResponse`` has
            already checked that every combine group names a real sub-query and
            every post-combine op a real group, so the references here hold.

    Returns:
        The DAG, with its layers populated by ``ExecutionDAG``.

    Raises:
        ValueError: If the graph is not acyclic, or an edge names a node that
            is not in it. Neither is reachable from a valid response; they are
            the last line of defence before the router walks the layers.
    """
    nodes: List[LogicalNode] = []
    edges: List[LogicalEdge] = []

    # A scan node carries no attributes: its id is its sub-query's id, so
    # everything about it is one lookup away in the decomposer response.
    for sq in response.sub_queries:
        nodes.append(LogicalNode(node_id=sq.id, kind="scan", inputs=[]))

    combine_node_ids: Dict[str, str] = {}
    for cg in response.combine_groups:
        combine_id = f"combine_{cg.group_id}"
        combine_node_ids[cg.group_id] = combine_id
        nodes.append(
            LogicalNode(
                node_id=combine_id,
                kind="combine",
                inputs=[i.subquery_id for i in cg.inputs],
                attributes={
                    "operation": cg.operation,
                    "group_id": cg.group_id,
                    "inputs": [i.model_dump() for i in cg.inputs],
                    "join_keys": [jk.model_dump() for jk in cg.join_keys],
                },
            )
        )
        for inp in cg.inputs:
            edges.append(
                LogicalEdge(
                    edge_id=f"edge_{inp.subquery_id}_{combine_id}",
                    from_id=inp.subquery_id,
                    to_id=combine_id,
                    role=inp.role,
                )
            )

    for op in response.post_combine_ops:
        target_combine_id = combine_node_ids.get(op.target_group_id)
        if not target_combine_id:
            raise ValueError(f"PostCombineOp references unknown combine group: {op.target_group_id}")
        nodes.append(
            LogicalNode(
                node_id=op.op_id,
                kind=_POST_OP_KINDS[op.operation],
                inputs=[target_combine_id],
                attributes={
                    "target_group_id": op.target_group_id,
                    "operation": op.operation,
                    "filters": [f.model_dump() for f in op.filters],
                    "metrics": [m.model_dump() for m in op.metrics],
                    "group_by": [g.model_dump() for g in op.group_by],
                    "order_by": [o.model_dump() for o in op.order_by],
                    "limit": op.limit,
                    "expected_schema": [c.model_dump() for c in op.expected_schema],
                    "metadata": op.metadata,
                },
            )
        )
        edges.append(
            LogicalEdge(
                edge_id=f"edge_{target_combine_id}_{op.op_id}",
                from_id=target_combine_id,
                to_id=op.op_id,
            )
        )

    node_ids = {n.node_id for n in nodes}
    for edge in edges:
        if edge.from_id not in node_ids or edge.to_id not in node_ids:
            raise ValueError(f"Edge references unknown node: {edge}")

    return ExecutionDAG(
        nodes=sorted(nodes, key=lambda n: n.node_id),
        edges=sorted(edges, key=lambda e: (e.from_id, e.to_id, e.role or "")),
    )
