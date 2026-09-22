from __future__ import annotations

from typing import Any, Callable, Dict, FrozenSet, List, Optional

from langchain_core.runnables import Runnable, RunnableConfig

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.nodes.global_planner.schemas import ExecutionDAG
from nl2sql.pipeline.plan_cache import PlanCache
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.pipeline.subgraphs import SubgraphOutput
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
import logging

SQL_AGENT_SUBGRAPH = "sql_agent"
SQL_AGENT_REQUIRED_CAPABILITIES = {DatasourceCapability.SUPPORTS_SQL.value}


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
    completed_node_ids: FrozenSet[str] = frozenset(),
) -> List[str]:
    node_index = {n.node_id: n for n in dag.nodes}
    for layer in dag.layers or []:
        pending_scan = [
            node_id
            for node_id in layer
            if node_id in node_index
            and node_index[node_id].kind == "scan"
            and node_id not in artifact_refs
            and node_id not in completed_node_ids
        ]
        if pending_scan:
            return pending_scan
    return []


def completed_scan_ids(subgraph_outputs: Optional[Dict[str, Any]]) -> FrozenSet[str]:
    """Sub-query ids that already ran, with or without an artifact.

    Subgraph ids are formatted ``name:node_id:trace_id``, so the node id is the
    second segment. A scan that failed produces no artifact; without this the
    layer router would re-dispatch it forever.
    """
    return frozenset(
        key.split(":")[1] for key in (subgraph_outputs or {}) if key.count(":") >= 2
    )


def resolve_subgraph(
    datasource_id: str,
    ctx: NL2SQLContext,
) -> Optional[str]:
    """Returns the subgraph able to serve a datasource, or None if none can.

    The sql_agent subgraph is the only one, and it requires SUPPORTS_SQL. A
    datasource without that capability resolves to None so the caller fails
    instead of routing it into SQL generation.
    """
    try:
        caps = ctx.ds_registry.get_capabilities(datasource_id)
    except Exception:
        return None

    if SQL_AGENT_REQUIRED_CAPABILITIES.issubset(caps):
        return SQL_AGENT_SUBGRAPH
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

def _as_warning(error: PipelineError, sub_query_id: str) -> Dict[str, Any]:
    """An error from a sub-query that succeeded, as a run warning.

    Either an earlier attempt's error that a retry recovered from, or a
    non-blocking warning. The original severity is kept.
    """
    return {
        "node": error.node,
        "message": error.message,
        "error_code": getattr(error.error_code, "value", error.error_code),
        "severity": getattr(error.severity, "value", error.severity),
        "sub_query_id": sub_query_id,
    }


def wrap_subgraph(
    subgraph: Runnable,
    subgraph_name: str,
    ctx: NL2SQLContext,
    execute: bool = True,
) -> Callable[Dict[str, Any]]:
    """Run a sub-query's subgraph and report it to the parent graph.

    The status reflects the final attempt, not the error history: the
    sub-query succeeded if it ended with SQL (and, when ``execute`` is True,
    with a result artifact), otherwise it failed. ``SubgraphOutput.errors``
    keeps every attempt's errors. When the sub-query succeeded, those errors
    were superseded by a later attempt, so they reach the run's ``warnings``
    rather than its ``errors``.
    """
    def _wrapper(state_dict: dict, config: Optional[RunnableConfig] = None) -> Dict[str, Any]:
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
        # Propagate the run config (carrying the cancellation token) into the subgraph.
        result = subgraph.invoke(sub_state.model_dump(), config=config)

        returned_state = SubgraphExecutionState.model_validate(result)

        executor_response = returned_state.executor_response
        planner_response = returned_state.ast_planner_response
        generator_response = returned_state.generator_response
        sub_reasoning = returned_state.reasoning
        artifact = executor_response.artifact if executor_response else None
        artifact_refs: Dict[str, Any] = {sub_query.id: artifact} if artifact else {}

        retry_count = returned_state.retry_count
        sql_draft = generator_response.sql_draft if generator_response else None
        succeeded = bool(sql_draft) and (artifact is not None or not execute)
        status = "success" if succeeded else "error"
        errors = list(returned_state.errors)
        blocking = [e for e in errors if e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL)]
        if not succeeded and not blocking:
            errors.append(
                PipelineError(
                    node=subgraph_name,
                    message=(
                        f"Sub-query '{sub_query.id}' ended without SQL."
                        if not sql_draft
                        else f"Sub-query '{sub_query.id}' ended without a result."
                    ),
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_SQL if not sql_draft else ErrorCode.EXECUTION_FAILED,
                )
            )
        plan = planner_response.plan if planner_response else None
        plan_source = planner_response.plan_source if planner_response else "llm"
        # Only a plan that passed validation and executed is cached; a refused,
        # failed or plan-only one never is. A cache hit is already stored.
        executed_cleanly = executor_response is not None and not any(
            e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL) for e in executor_response.errors
        )
        if succeeded and execute and executed_cleanly and plan is not None and plan_source == "llm":
            PlanCache(getattr(ctx, "schema_store", None)).put(sub_query, plan)

        subgraph_output = SubgraphOutput(
            sub_query=sub_query,
            subgraph_name=subgraph_name,
            subgraph_id=subgraph_id,
            retry_count=retry_count,
            plan=plan,
            plan_source=plan_source,
            sql_draft=sql_draft,
            artifact=artifact,
            errors=errors,
            validation=(
                returned_state.logical_validator_response.checks
                if returned_state.logical_validator_response
                else []
            ),
            reasoning=sub_reasoning,
            status=status,
        )

        return {
            "artifact_refs": artifact_refs,
            "subgraph_outputs": {subgraph_id: subgraph_output},
            "errors": [] if succeeded else errors,
            "warnings": [_as_warning(e, sub_query.id) for e in errors] if succeeded else [],
            "reasoning": returned_state.reasoning,
        }

    return _wrapper
