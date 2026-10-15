from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any, List

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.hooks import hooks_of
from nl2sql.policies import UserContext
from nl2sql.policies.scope import default_schema
from nl2sql.runtime import Runtime
from nl2sql.execution.contracts import ExecutorRequest
from nl2sql.execution.executor import SqlExecutorService
from nl2sql.pipeline.nodes.executor.value_check import check_values
from nl2sql_sdk.capabilities import DatasourceCapability

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState

logger = get_logger("executor")


class ExecutorNode:
    """Thin wrapper that delegates to executor services based on capabilities."""

    def __init__(self, ctx: Runtime):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.ds_registry = ctx.ds_registry
        self.executor = SqlExecutorService(ctx.ds_registry, ctx.artifact_store,
                                          fetch_batch_size=ctx.settings.results.fetch_batch_size)
        self.max_retries = ctx.settings.pipeline.retries.max
        self.tenant_id = ctx.tenant_id
        self.policy_engine = ctx.policy_engine
        self.hooks = hooks_of(ctx)

    def _supports_sql(self, ds_id: str) -> bool:
        """Reports whether a datasource may be executed as SQL.

        The SQL executor is the only executor, so a datasource that does not
        declare SUPPORTS_SQL -- or whose adapter cannot report capabilities --
        gets no executor at all rather than being run as SQL anyway. The
        registry's check fails closed.
        """
        return self.ds_registry.supports(ds_id, DatasourceCapability.SUPPORTS_SQL)

    def _check_values(self, state: SubgraphExecutionState, ds_id: str, response) -> List[PipelineError]:
        """``VALUE_NOT_FOUND`` for an empty result filtered on a value that does not exist.

        Runs only when the query succeeded with no rows. One retry is spent on
        it: a later empty result reports the value as missing instead. Only tables the caller's scope allows are probed. A failure here never
        fails the run.
        """
        if response.errors or response.metrics.get("row_count") != 0:
            return []
        planner_response = getattr(state, "ast_planner_response", None)
        plan = planner_response.plan if planner_response else None
        if plan is None:
            return []
        try:
            scope = self.policy_engine.scope(state.user_context, ds_id)
            already_retried = any(e.error_code == ErrorCode.VALUE_NOT_FOUND for e in state.errors)
            can_retry = not already_retried and state.retry_count < self.max_retries
            errors = check_values(
                plan,
                self.ds_registry.get_adapter(ds_id),
                lambda ref: scope.allows(ref.schema_name, ref.name),
                can_retry,
                self.ds_registry.get_datasource(ds_id).limits.row_limit,
            )
            # The SQL agent retries only on this attempt's own finding.
            return [e.model_copy(update={"details": {"attempt": state.retry_count}}) for e in errors]
        except Exception as exc:
            logger.warning(f"Value check skipped: {exc}")
            return []

    def _hook(self, point: str, state: SubgraphExecutionState, ds_id: str, sql: str, **fields: Any):
        """``execute.before`` or ``execute.after`` for this sub-query's SQL."""
        planner_response = getattr(state, "ast_planner_response", None)
        plan = planner_response.plan if planner_response else None
        tables = None
        if plan is not None:
            tables = [f"{ds_id}.{t.schema_name or default_schema(self.ds_registry, ds_id)}.{t.name}"
                      for t in plan.tables]
        return self.hooks.call(point, trace_id=state.trace_id, question=getattr(state, "user_query", "") or "",
                               user=state.user_context or UserContext(), datasource_id=ds_id,
                               sub_query=state.sub_query, plan=plan, sql=sql, tables=tables, **fields)

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        try:
            ds_id = state.sub_query.datasource_id 
            sql = state.generator_response.sql_draft 

            if not sql:
                error = PipelineError(
                    node=self.node_name,
                    message="No SQL to execute.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_SQL,
                )
                return {"executor_response": None, "errors": [error]}

            if not ds_id:
                error = PipelineError(
                    node=self.node_name,
                    message="No datasource_id in state.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_DATASOURCE_ID,
                )
                return {"executor_response": None, "errors": [error]}

            executor = self.executor if self._supports_sql(ds_id) else None
            if executor is None:
                error = PipelineError(
                    node=self.node_name,
                    message=f"No executor available for datasource '{ds_id}'.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_STATE,
                )
                return {"executor_response": None, "errors": [error]}

            request = ExecutorRequest(
                node_id=state.sub_query.id,
                trace_id=state.trace_id,
                subgraph_name=state.subgraph_name,
                datasource_id=ds_id,
                schema_version=state.sub_query.schema_version,
                sql=sql,
                user_context=state.user_context,
                tenant_id=self.tenant_id,
            )
            # execute.before: after the validator, a hook may only stop the SQL.
            verdict = self._hook("execute.before", state, ds_id, sql)
            if not verdict.allowed:
                return {"executor_response": None, "errors": [verdict.error(self.node_name)]}
            response = executor.execute(request)
            if not response.errors and response.artifact is not None:
                generator_response = getattr(state, "generator_response", None)
                row_cap = getattr(generator_response, "row_cap", None)
                summary = ResultSummary(node_id=state.sub_query.id, columns=list(response.artifact.columns),
                                        row_count=response.artifact.row_count,
                                        truncated=bool(row_cap and response.artifact.row_count >= row_cap))
                verdict = self._hook("execute.after", state, ds_id, sql, result=summary)
                if not verdict.allowed:
                    return {"executor_response": None, "errors": [verdict.error(self.node_name)]}
            value_errors = self._check_values(state, ds_id, response)
            return {
                "executor_response": response,
                "errors": response.errors + value_errors,
                "reasoning": response.reasoning,
            }
        except Exception as exc:
            logger.error(f"Node {self.node_name} failed: {exc}")
            error = PipelineError(
                node=self.node_name,
                message=f"Executor crash: {exc}",
                severity=ErrorSeverity.CRITICAL,
                error_code=ErrorCode.EXECUTOR_CRASH,
            )
            return {
                "executor_response": None,
                "errors": [error],
            }
