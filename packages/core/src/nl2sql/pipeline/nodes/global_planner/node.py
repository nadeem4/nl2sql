from __future__ import annotations
from typing import Dict, Any, TYPE_CHECKING, List

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import (
    RelationSchema,
    ColumnSpec,
    ExecutionDAG,
    LogicalNode,
    LogicalEdge,
)
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from .schemas import GlobalPlannerResponse

logger = get_logger("global_planner")


class GlobalPlannerNode:
    """Generates a deterministic execution plan for aggregating sub-query results.
    
    This node runs AFTER the Decomposer and BEFORE the SQL Agents.
    It does not execute SQL, but produces a blueprint (ResultPlan) for the Aggregator.
    """

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the planning logic."""
        decomposer_response = state.decomposer_response
        sub_queries = decomposer_response.sub_queries if decomposer_response else []
        combine_groups = decomposer_response.combine_groups if decomposer_response else []
        post_combine_ops = decomposer_response.post_combine_ops if decomposer_response else []

        try:
            nodes: List[LogicalNode] = []
            edges: List[LogicalEdge] = []
            node_index: Dict[str, LogicalNode] = {}

            for sq in sub_queries:
                schema = RelationSchema(
                    columns=[ColumnSpec(name=c.name, dtype=c.dtype) for c in sq.expected_schema]
                )
                node = LogicalNode(
                    node_id=sq.id,
                    kind="scan",
                    inputs=[],
                    output_schema=schema,
                    attributes={
                        "datasource_id": sq.datasource_id,
                        "intent": sq.intent,
                        "metrics": [m.model_dump() for m in sq.metrics],
                        "filters": [f.model_dump() for f in sq.filters],
                        "group_by": [g.model_dump() for g in sq.group_by],
                        "expected_schema": [c.model_dump() for c in sq.expected_schema],
                    },
                )
                nodes.append(node)
                node_index[node.node_id] = node

            combine_node_ids: Dict[str, str] = {}
            for cg in combine_groups:
                combine_id = f"combine_{cg.group_id}"
                combine_node_ids[cg.group_id] = combine_id
                input_ids = [i.subquery_id for i in cg.inputs]
                output_schema = (
                    node_index[input_ids[0]].output_schema
                    if input_ids and input_ids[0] in node_index
                    else RelationSchema(columns=[])
                )
                node = LogicalNode(
                    node_id=combine_id,
                    kind="combine",
                    inputs=input_ids,
                    output_schema=output_schema,
                    attributes={
                        "operation": cg.operation,
                        "group_id": cg.group_id,
                        "inputs": [i.model_dump() for i in cg.inputs],
                        "join_keys": [jk.model_dump() for jk in cg.join_keys],
                    },
                )
                nodes.append(node)
                node_index[node.node_id] = node

                for inp in cg.inputs:
                    edges.append(
                        LogicalEdge(
                            edge_id=f"edge_{inp.subquery_id}_{combine_id}",
                            from_id=inp.subquery_id,
                            to_id=combine_id,
                            role=inp.role,
                        )
                    )

            for op in post_combine_ops:
                target_combine_id = combine_node_ids.get(op.target_group_id)
                if not target_combine_id:
                    raise ValueError(f"PostCombineOp references unknown combine group: {op.target_group_id}")
                output_schema = RelationSchema(
                    columns=[ColumnSpec(name=c.name, dtype=c.dtype) for c in op.expected_schema]
                )
                kind_map = {
                    "filter": "post_filter",
                    "aggregate": "post_aggregate",
                    "project": "post_project",
                    "sort": "post_sort",
                    "limit": "post_limit",
                }
                node = LogicalNode(
                    node_id=op.op_id,
                    kind=kind_map[op.operation],
                    inputs=[target_combine_id],
                    output_schema=output_schema,
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
                nodes.append(node)
                node_index[node.node_id] = node
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

            execution_dag = ExecutionDAG(
                nodes=sorted(nodes, key=lambda n: n.node_id),
                edges=sorted(edges, key=lambda e: (e.from_id, e.to_id, e.role or "")),
            )

            execution_dag.content_hash = self._hash_execution_dag(execution_dag)
            execution_dag.dag_id = f"dag_{execution_dag.content_hash[:12]}"

            return {
                "global_planner_response": GlobalPlannerResponse(execution_dag=execution_dag),
                "reasoning": [{"node": self.node_name, "content": "Built explicit execution DAG."}],
            }

        except Exception as e:
            logger.error(f"GlobalPlanner failed: {e}")
            return {
                "global_planner_response": GlobalPlannerResponse(execution_dag=None),
                "reasoning": [{"node": self.node_name, "content": f"Planning failed: {e}", "type": "error"}],
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message=f"Global Plan generation failed: {str(e)}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.PLANNER_FAILED 
                    )
                ]
            }

    def _hash_execution_dag(self, dag: ExecutionDAG) -> str:
        import hashlib
        import json

        payload = {
            "nodes": [n.model_dump() for n in dag.nodes],
            "edges": [e.model_dump() for e in dag.edges],
            "version": dag.version,
        }
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
