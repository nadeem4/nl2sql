from __future__ import annotations

from typing import Dict

from langgraph.graph import END
from langgraph.types import Send

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.graph_utils import (
    StateAccessor,
    build_scan_payload,
    next_scan_layer_ids,
    resolve_subgraph,
)
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.subgraphs import SubgraphSpec
from nl2sql.common.logger import get_logger

logger = get_logger("router")


def resolver_route(state: GraphState) -> str:
    accessor = StateAccessor(state)
    resolver_response = accessor.get("datasource_resolver_response")
    if not resolver_response:
        return "end"
    if not resolver_response.resolved_datasources or not resolver_response.allowed_datasource_ids:
        return "end"
    return "continue"


def build_scan_layer_router(
    ctx: NL2SQLContext,
    subgraph_specs: Dict[str, SubgraphSpec],
):
    def route_scan_layers(state: GraphState):
        global_planner_response = state.global_planner_response
        dag = global_planner_response.execution_dag if global_planner_response else None
        decomposer_response = state.decomposer_response
        sub_queries = decomposer_response.sub_queries if decomposer_response else []
        sub_query_map = {sq.id: sq for sq in sub_queries}
        artifact_refs = state.artifact_refs or {}
        if not dag or not dag.layers:
            return END

        node_index = {n.node_id: n for n in dag.nodes}
        target_ids = next_scan_layer_ids(dag, artifact_refs)
        if not target_ids:
            return [
                Send("aggregator",state)
            ]

        branches = []
        for node_id in target_ids:
            if node_id in sub_query_map:
                sq = sub_query_map[node_id]
                datasource_id = sq.datasource_id
            else:
                node = node_index.get(node_id) if dag else None
                if not node:
                    continue
                datasource_id = (node.attributes or {}).get("datasource_id")

            target = resolve_subgraph(datasource_id, ctx, subgraph_specs)
            if not target:
                raise PipelineError(
                    node="layer_router",
                    message=f"No compatible subgraph found for datasource '{datasource_id}'.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_STATE,
                )
            payload = build_scan_payload(state, target, node_id)
            branches.append(Send(target, payload))

        return branches

    return route_scan_layers
