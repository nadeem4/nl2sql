from __future__ import annotations

from typing import Dict, List, Tuple

import polars as pl

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.execution.contracts import ArtifactRef
from nl2sql.pipeline.nodes.global_planner.schemas import ExecutionDAG, LogicalNode, LogicalEdge

from .engines.base import AggregationEngine


class AggregationService:
    def __init__(self, engine: AggregationEngine):
        self.engine = engine

    def execute(
        self,
        dag: ExecutionDAG,
        artifact_refs: Dict[str, ArtifactRef],
    ) -> Dict[str, List[Dict]]:
        if not dag:
            raise PipelineError(
                node="aggregator",
                message="No ExecutionDAG found for aggregation.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED,
            )

        node_index: Dict[str, LogicalNode] = {n.node_id: n for n in dag.nodes}
        edges: List[LogicalEdge] = dag.edges
        incoming_edges: Dict[str, List[LogicalEdge]] = {}
        outgoing_edges: Dict[str, List[LogicalEdge]] = {}
        for edge in edges:
            incoming_edges.setdefault(edge.to_id, []).append(edge)
            outgoing_edges.setdefault(edge.from_id, []).append(edge)

        computed: Dict[str, pl.DataFrame] = {}

        for layer in dag.layers or []:
            for node_id in layer:
                node = node_index.get(node_id)
                if not node:
                    continue
                if node.kind == "scan":
                    artifact = artifact_refs.get(node_id)
                    if not artifact:
                        raise PipelineError(
                            node="aggregator",
                            message=f"Missing artifact for scan node {node_id}.",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.EXECUTION_ERROR,
                        )
                    computed[node_id] = self.engine.load_scan(artifact)
                    continue

                if node.kind == "combine":
                    upstream = incoming_edges.get(node_id, [])
                    inputs = self._ordered_inputs(upstream, computed)
                    computed[node_id] = self.engine.combine(
                        node.attributes.get("operation"),
                        inputs,
                        node.attributes.get("join_keys", []),
                    )
                    continue

                if node.kind.startswith("post_"):
                    input_ids = node.inputs or []
                    if len(input_ids) != 1 or input_ids[0] not in computed:
                        raise PipelineError(
                            node="aggregator",
                            message=f"Post-combine node '{node_id}' expects a single input.",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.AGGREGATOR_FAILED,
                        )
                    computed[node_id] = self.engine.post_op(
                        node.attributes.get("operation"),
                        computed[input_ids[0]],
                        node.attributes,
                    )
                    continue

                raise PipelineError(
                    node="aggregator",
                    message=f"Unsupported logical node kind '{node.kind}'.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.AGGREGATOR_FAILED,
                )

        terminal_nodes = sorted([n.node_id for n in dag.nodes if n.node_id not in outgoing_edges])
        if not terminal_nodes:
            raise PipelineError(
                node="aggregator",
                message="No terminal nodes found in DAG.",
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.AGGREGATOR_FAILED,
            )

        return {
            node_id: self.engine.to_rows(computed[node_id])
            for node_id in terminal_nodes
            if node_id in computed
        }

    def _ordered_inputs(
        self,
        edges: List[LogicalEdge],
        computed: Dict[str, pl.DataFrame],
    ) -> List[Tuple[str, pl.DataFrame]]:
        def role_rank(role: str) -> int:
            order = {"left": 0, "base": 0, "primary": 0, "right": 1, "compare": 1, "secondary": 1}
            return order.get(role or "", 2)

        ordered = sorted(edges, key=lambda e: (role_rank(e.role), e.from_id))
        return [(edge.role or "", computed[edge.from_id]) for edge in ordered if edge.from_id in computed]
