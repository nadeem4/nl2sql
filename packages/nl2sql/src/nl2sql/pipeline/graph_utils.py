from __future__ import annotations

from typing import Any, Callable, Dict, FrozenSet, List, Optional

from langchain_core.runnables import Runnable, RunnableConfig

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError, RunWarning
from nl2sql.hooks import hooks_of
from nl2sql.hooks.approval import ApprovalRequest
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime
from nl2sql.execution.dag import ExecutionDAG
from nl2sql.pipeline.plan_cache import plan_cache_for, planner_of
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.pipeline.subgraphs import SubgraphOutput
from nl2sql_sdk.capabilities import DatasourceCapability
import logging

SQL_AGENT_SUBGRAPH = "sql_agent"
SQL_AGENT_REQUIRED_CAPABILITIES = {DatasourceCapability.SUPPORTS_SQL.value}


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
    ctx: Runtime,
) -> Optional[str]:
    """Returns the subgraph able to serve a datasource, or None if none can.

    The sql_agent subgraph is the only one, and it requires SUPPORTS_SQL. A
    datasource without that capability resolves to None so the caller fails
    instead of routing it into SQL generation.
    """
    if ctx.ds_registry.supports(datasource_id, *SQL_AGENT_REQUIRED_CAPABILITIES):
        return SQL_AGENT_SUBGRAPH
    return None


def build_scan_payload(
    state: GraphState,
    subgraph_name: str,
    node_id: str,
) -> Dict[str, Any]:
    trace_id = state.trace_id
    pinned = getattr(state, "pinned", None)
    pinned_plan = pinned.plans.get(node_id) if pinned is not None else None
    return {
        "subgraph_id": f"{subgraph_name}:{node_id}:{trace_id}",
        "subgraph_name": subgraph_name,
        "trace_id": trace_id,
        "user_query": getattr(state, "user_query", ""),
        "user_context": state.user_context,
        "decomposer_response": state.decomposer_response,
        "datasource_resolver_response": state.datasource_resolver_response,
        "pinned_plan": pinned_plan,
        "pinned_source": pinned.source if pinned_plan is not None else None,
        "approvals": list(getattr(state, "approvals", None) or []),
        "clarify": getattr(state, "clarify", True),
    }

def _as_warning(error: PipelineError, sub_query_id: str) -> RunWarning:
    """An error from a sub-query that succeeded, as a run warning.

    Either an earlier attempt's error that a retry recovered from (marked
    ``detail["recovered"]``), or a non-blocking warning.
    """
    return RunWarning.of(error, sub_query_id)


def _approval_requests(ctx: Runtime, errors: List[PipelineError], state_dict: Dict[str, Any], sub_query: Any,
                       planner_response: Any, sql: Optional[str]) -> List[ApprovalRequest]:
    """The request for each ``NEEDS_APPROVAL`` this sub-query ended with (one hook at most asks).

    It pins the run's decomposition, the sub-query and its plan as
    ``plan.after`` got it (``planner_plan``): the re-run passes that plan
    through ``plan.after`` again, so a hook's change is applied once, as on
    this run, and the SQL generated again can match the hash.
    """
    asked = [e for e in errors if e.error_code == ErrorCode.NEEDS_APPROVAL]
    if not asked or not sql:
        return []
    hooks = hooks_of(ctx)
    plan = planner_response.planner_plan if planner_response else None
    requests = []
    for error in asked:
        details = error.details or {}
        hook = details.get("hook") or ""
        requests.append(ApprovalRequest.build(
            question=state_dict.get("user_query") or "", asker=state_dict.get("user_context") or UserContext(),
            hook=hook, datasource_id=sub_query.datasource_id, sql=sql, reason=details.get("reason") or "",
            approver_roles=hooks.approver_roles(hook), decomposition=state_dict.get("decomposer_response"),
            sub_query=sub_query, plan=plan))
    return requests


def wrap_subgraph(
    subgraph: Runnable,
    subgraph_name: str,
    ctx: Runtime,
    execute: bool = True,
) -> Callable[Dict[str, Any]]:
    """Run a sub-query's subgraph and report it to the parent graph.

    The status reflects the final attempt, not the error history: the
    sub-query succeeded if it ended with SQL (and, when ``execute`` is True,
    with a result artifact), otherwise it failed. ``SubgraphOutput.errors``
    keeps every attempt's errors. When the sub-query succeeded, those errors
    were superseded by a later attempt, so they reach the run's ``warnings``
    rather than its ``errors``.

    The clarifier may ask which reading was meant only when the run's
    ``clarify`` (in the scan payload) is on and the sub-query is the run's
    only one (D47). A
    sub-query that asked ends with status ``needs_clarification`` and its
    ``NEEDS_CLARIFICATION`` (INFO) as its error; no SQL is missing.
    """
    # Read with the graph, so the plan is keyed on the planner model this run uses.
    planner = planner_of(ctx)

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
            user_query=state_dict.get("user_query") or "",
            user_context=state_dict.get("user_context"),
            sub_query=sub_query,
            subgraph_id=subgraph_id,
            subgraph_name=subgraph_name,
            pinned_plan=state_dict.get("pinned_plan"),
            pinned_source=state_dict.get("pinned_source"),
            approvals=state_dict.get("approvals") or [],
            clarify=bool(state_dict.get("clarify", True)) and len(decomposer_response.sub_queries) == 1,
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
        asked = any(e.error_code == ErrorCode.NEEDS_CLARIFICATION for e in errors)
        if asked:
            status = "needs_clarification"
        elif not succeeded and not blocking:
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
        # Only a sub-query that passed validation and executed caches a plan;
        # a refused, failed or plan-only one never does, and a cache hit is
        # already stored. What is cached is the planner's plan, never a
        # plan.after hook's change of it. When a hook changed it, the planner's
        # plan was not itself validated in this run; that is safe, because a
        # cached plan goes through plan.after and the validator again on every
        # hit.
        executed_cleanly = executor_response is not None and not any(
            e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL) for e in executor_response.errors
        )
        planner_plan = planner_response.planner_plan if planner_response else None
        if succeeded and execute and executed_cleanly and planner_plan is not None and plan_source == "llm":
            plan_cache_for(ctx, planner).put(sub_query, planner_plan)
        # A hook's deny scoped to this sub-query drops it: the run goes on
        # without it, and the answer says what went unanswered.
        dropped = [e for e in errors if e.error_code == ErrorCode.HOOK_DENIED
                   and (e.details or {}).get("scope") == "sub_query"]
        if dropped and not succeeded:
            status = "dropped"

        row_cap = generator_response.row_cap if generator_response else None
        subgraph_output = SubgraphOutput(
            sub_query=sub_query,
            subgraph_name=subgraph_name,
            subgraph_id=subgraph_id,
            retry_count=retry_count,
            plan=plan,
            plan_source=plan_source,
            sql_draft=sql_draft,
            row_cap=row_cap,
            truncated=bool(row_cap and artifact is not None and artifact.row_count >= row_cap),
            artifact=artifact,
            errors=errors,
            validation=(
                returned_state.logical_validator_response.checks
                if returned_state.logical_validator_response
                else []
            ),
            reasoning=sub_reasoning,
            status=status,
            knowledge_used=returned_state.knowledge_used,
            ambiguous_phrase=planner_response.ambiguous_phrase if planner_response else None,
            alternatives=list(planner_response.alternatives) if planner_response else [],
        )

        if status == "dropped":
            return {
                "subgraph_outputs": {subgraph_id: subgraph_output},
                "warnings": [_as_warning(e.model_copy(update={
                    "severity": ErrorSeverity.WARNING,
                    "message": f"Sub-query '{sub_query.id}' ({sub_query.intent}) was not run: {e.message}",
                }), sub_query.id) for e in dropped],
                "reasoning": returned_state.reasoning,
            }
        return {
            "artifact_refs": artifact_refs,
            "subgraph_outputs": {subgraph_id: subgraph_output},
            "approvals_needed": [] if succeeded else _approval_requests(
                ctx, errors, state_dict, sub_query, planner_response, sql_draft),
            "errors": [] if succeeded else errors,
            "warnings": [_as_warning(e, sub_query.id) for e in errors] if succeeded else [],
            "reasoning": returned_state.reasoning,
        }

    return _wrapper
