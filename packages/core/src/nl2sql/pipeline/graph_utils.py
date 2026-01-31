from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from langchain_core.runnables import Runnable

from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.nodes.global_planner.schemas import ExecutionDAG
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.pipeline.subgraphs import SubgraphOutput, SubgraphSpec
import logging


class StateAccessor:
    """Adapter for GraphState/dict access to simplify routing logic."""

    def __init__(self, state: Any):
        self._state = state

    def get(self, key: str, default: Any = None) -> Any:
        if isinstance(self._state, dict):
            return self._state.get(key, default)
        return getattr(self._state, key, default)


def next_scan_layer_ids(
    dag: ExecutionDAG,
    artifact_refs: Dict[str, Any],
) -> List[str]:
    node_index = {n.node_id: n for n in dag.nodes}
    for layer in dag.layers or []:
        pending_scan = [
            node_id
            for node_id in layer
            if node_id in node_index
            and node_index[node_id].kind == "scan"
            and node_id not in artifact_refs
        ]
        if pending_scan:
            return pending_scan
    return []


def resolve_subgraph(
    datasource_id: str,
    ctx: NL2SQLContext,
    subgraph_specs: Dict[str, SubgraphSpec],
) -> Optional[str]:
    try:
        caps = ctx.ds_registry.get_capabilities(datasource_id)
    except Exception:
        return None

    for name, spec in subgraph_specs.items():
        if spec.required_capabilities.issubset(caps):
            return name
    return None


def build_scan_payload(
    state: GraphState,
    subgraph_name: str,
    node_id: str,
) -> Dict[str, Any]:
    trace_id = state.trace_id   
    return {
        "subgraph_id": f"{subgraph_name}:{node_id}:{trace_id}",
        "subgraph_name": subgraph_name,
        "trace_id": trace_id,
        "user_context": state.user_context,
        "decomposer_response": state.decomposer_response,
        "datasource_resolver_response": state.datasource_resolver_response,
    }

def wrap_subgraph(
    subgraph: Runnable,
    subgraph_name: str,
    ctx: NL2SQLContext,
) -> Callable[Dict[str, Any]]:
    def _wrapper(state_dict: dict) -> Dict[str, Any]:
        trace_id = state_dict.get("trace_id")
        subgraph_id = state_dict.get("subgraph_id")
        sub_query_id = subgraph_id.split(":")[1]
        sub_query = None
        decomposer_response = state_dict.get("decomposer_response")
        for sq in decomposer_response.sub_queries:
            if sq.id == sub_query_id:
                sub_query = sq
                break

        sub_state = SubgraphExecutionState(
            trace_id=trace_id,
            user_context=state_dict.get("user_context"),
            sub_query=sub_query,
            subgraph_id=subgraph_id,
            subgraph_name=subgraph_name,
        )
        result = subgraph.invoke(sub_state.model_dump())

        returned_state = SubgraphExecutionState.model_validate(result)

        executor_response = returned_state.executor_response
        planner_response = returned_state.ast_planner_response
        generator_response = returned_state.generator_response
        sub_reasoning = returned_state.reasoning
        artifact_refs: Dict[str, Any] = {}
        artifact = executor_response.artifact
        artifact_refs[sub_query.id] = artifact

        retry_count = returned_state.retry_count
        status = "error" if returned_state.errors else "success"
        subgraph_output = SubgraphOutput(
            sub_query=sub_query,
            subgraph_name=subgraph_name,
            subgraph_id=subgraph_id,
            retry_count=retry_count,
            plan=planner_response.plan,
            sql_draft=generator_response.sql_draft if generator_response else None,
            artifact=artifact,
            errors=returned_state.errors,
            reasoning=sub_reasoning,
            status=status,
        )

        return {
            "artifact_refs": artifact_refs,
            "subgraph_outputs": {subgraph_id: subgraph_output},
            "errors": returned_state.errors,
            "reasoning": returned_state.reasoning,
        }

    return _wrapper
