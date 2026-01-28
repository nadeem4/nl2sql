from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.execution.contracts import ExecutorRequest
from nl2sql.execution.executor import ExecutorRegistry
from nl2sql_adapter_sdk.capabilities import DatasourceCapability

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState

logger = get_logger("executor")


class ExecutorNode:
    """Thin wrapper that delegates to executor services based on capabilities."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.ds_registry = ctx.ds_registry
        self.registry = ExecutorRegistry(ctx.ds_registry)

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        try:
            ds_id = state.sub_query.datasource_id if state.sub_query else None
            sql = state.generator_response.sql_draft if state.generator_response else None

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

            adapter = self.ds_registry.get_adapter(ds_id)
            caps = adapter.capabilities() if hasattr(adapter, "capabilities") else {DatasourceCapability.SUPPORTS_SQL}
            executor = self.registry.get_executor(caps)
            if executor is None:
                error = PipelineError(
                    node=self.node_name,
                    message=f"No executor available for datasource '{ds_id}'.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_STATE,
                )
                return {"executor_response": None, "errors": [error]}

            request = ExecutorRequest(
                node_id=state.sub_query.id if state.sub_query else "unknown",
                trace_id=state.trace_id,
                subgraph_name=state.subgraph_name or "sql_agent",
                datasource_id=ds_id,
                schema_version=getattr(state.sub_query, "schema_version", None),
                sql=sql,
                user_context=state.user_context,
            )
            response = executor.execute(request)
            return {
                "executor_response": response,
                "errors": response.errors,
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
