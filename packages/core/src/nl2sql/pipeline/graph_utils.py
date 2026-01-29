from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from langchain_core.runnables import Runnable

from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.nodes.global_planner.schemas import ExecutionDAG
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.pipeline.subgraphs import SubgraphOutput, SubgraphSpec


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
    accessor = StateAccessor(state)
    trace_id = accessor.get("trace_id")
    return {
        "subgraph_id": f"{subgraph_name}:{node_id}:{trace_id}",
        "subgraph_name": subgraph_name,
        "trace_id": trace_id,
        "user_context": accessor.get("user_context"),
        "decomposer_response": accessor.get("decomposer_response"),
        "datasource_resolver_response": accessor.get("datasource_resolver_response"),
    }


def _get_response_value(response: Any, attr: str) -> Any:
    if response is None:
        return None
    if hasattr(response, attr):
        return getattr(response, attr)
    if isinstance(response, dict):
        return response.get(attr)
    return None


def wrap_subgraph(
    subgraph: Runnable,
    subgraph_name: str,
    ctx: NL2SQLContext,
) -> Callable[[GraphState], Dict[str, Any]]:
    def _wrapper(state: GraphState) -> Dict[str, Any]:
        accessor = StateAccessor(state)
        trace_id = accessor.get("trace_id")
        subgraph_id = accessor.get("subgraph_id")
        trace_id = trace_id or "unknown"
        sub_query_id = subgraph_id.split(":")[1] if ":" in subgraph_id else None
        selected_datasource_id = None
        sub_query = None
        decomposer_response = accessor.get("decomposer_response")
        for sq in (decomposer_response.sub_queries if decomposer_response else []):
            if sq.id == sub_query_id:
                sub_query = sq
                selected_datasource_id = sq.datasource_id
                break

        sub_state = SubgraphExecutionState(
            trace_id=trace_id,
            user_context=accessor.get("user_context"),
            sub_query=sub_query,
            subgraph_id=subgraph_id,
            subgraph_name=subgraph_name,
        )
        result = subgraph.invoke(sub_state.model_dump())

        executor_response = result.get("executor_response")
        generator_response = result.get("generator_response")
        planner_response = result.get("ast_planner_response")

        sq_id = (
            result.get("sub_query_id")
            or (sub_query.id if sub_query else None)
            or sub_query_id
            or "unknown"
        )
        sub_reasoning = result.get("reasoning", [])
        datasource_id = result.get("selected_datasource_id") or selected_datasource_id
        if sub_query:
            datasource_id = sub_query.datasource_id

        schema_version = None
        resolver_response = accessor.get("datasource_resolver_response")
        if resolver_response:
            for entry in resolver_response.resolved_datasources:
                entry_id = getattr(entry, "datasource_id", None)
                if not entry_id and isinstance(entry, dict):
                    entry_id = entry.get("datasource_id")
                if entry_id == datasource_id:
                    if hasattr(entry, "schema_version"):
                        schema_version = entry.schema_version
                    elif isinstance(entry, dict):
                        schema_version = entry.get("schema_version")
                    break

        artifact_refs: Dict[str, Any] = {}
        artifact = _get_response_value(executor_response, "artifact")
        if artifact:
            ctx.execution_store.put(sq_id, artifact)
            artifact_refs[sq_id] = artifact

        retry_count = result.get("retry_count", 0)
        status = "error" if result.get("errors") else "success"
        subgraph_output = SubgraphOutput(
            subgraph_id=subgraph_id,
            subgraph_name=subgraph_name,
            sub_query_id=sq_id,
            selected_datasource_id=datasource_id,
            schema_version=schema_version,
            retry_count=retry_count,
            plan=_get_response_value(planner_response, "plan"),
            sql_draft=_get_response_value(generator_response, "sql_draft"),
            artifact=artifact,
            errors=result.get("errors", []),
            reasoning=sub_reasoning,
            status=status,
        )

        return {
            "artifact_refs": artifact_refs,
            "subgraph_outputs": {subgraph_id: subgraph_output},
            "errors": result.get("errors", []),
            "reasoning": sub_reasoning,
        }

    return _wrapper
