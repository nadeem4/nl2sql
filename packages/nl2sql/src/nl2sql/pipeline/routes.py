from __future__ import annotations

from langgraph.graph import END
from langgraph.types import Send

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.exceptions import PipelineExecutionError
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.graph_utils import (
    build_scan_payload,
    completed_scan_ids,
    next_scan_layer_ids,
    resolve_subgraph,
)
from nl2sql.pipeline.state import GraphState
from nl2sql.common.logger import get_logger

logger = get_logger("router")


def resolver_route(state: GraphState) -> str:
    resolver_response = state.datasource_resolver_response
    if not resolver_response:
        return "end"
    if not resolver_response.resolved_datasources or not resolver_response.allowed_datasource_ids:
        return "end"
    return "continue"


def build_scan_layer_router(ctx: NL2SQLContext, execute: bool = True):
    """Builds the router that fans sub-queries out and then moves on.

    When ``execute`` is False the run stops once every scan has produced a
    plan and SQL: aggregation and synthesis need executed rows, so the router
    returns ``END`` instead of dispatching the aggregator.

    For the same reason it also returns ``END`` when every scan has finished
    and none produced an artifact -- each sub-query was denied or failed.
    Aggregating would only fail on the missing artifact, and synthesizing
    would spend an LLM call explaining nothing, so the run's errors stay the
    sub-queries' own, led by the real cause.
    """

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
        target_ids = next_scan_layer_ids(
            dag, artifact_refs, completed_scan_ids(state.subgraph_outputs)
        )
        if not target_ids:
            if not execute or not artifact_refs:
                return END
            return [Send("aggregator", state)]

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

            target = resolve_subgraph(datasource_id, ctx)
            if not target:
                raise PipelineExecutionError(
                    PipelineError(
                        node="layer_router",
                        message=f"No compatible subgraph found for datasource '{datasource_id}'.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.INVALID_STATE,
                    )
                )
            payload = build_scan_payload(state, target, node_id)
            branches.append(Send(target, payload))

        return branches

    return route_scan_layers
