from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any
from nl2sql.datasources import DatasourceRegistry
from nl2sql_adapter_sdk import QueryResult as SdkExecutionResult

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import ExecutionModel
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.security import enforce_read_only
from nl2sql.common.logger import get_logger

logger = get_logger("executor")


class ExecutorNode:
    """Executes the generated SQL query via the Datasource Adapter.

    Attributes:
        registry (DatasourceRegistry): The registry of datasources.
    """

    def __init__(self, registry: DatasourceRegistry):
        """Initializes the ExecutorNode.

        Args:
            registry (DatasourceRegistry): The registry of datasources.
        """
        self.registry = registry

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the generated SQL against the selected datasource.

        This method performs the following steps:
        1. Validates presence of SQL and datasource ID.
        2. Retrieves the appropriate adapter.
        3. optionally performs cost estimation checks (safeguards).
        4. Executes the SQL query.
        5. Formats the results into an ExecutionModel.

        Args:
            state (GraphState): The current state of the execution graph.

        Returns:
            Dict[str, Any]: A dictionary containing the 'execution' result,
                any 'errors', and 'reasoning' logs.
        """
        node_name = f"{self.__class__.__name__} ({state.selected_datasource_id}) ({state.user_query})"

        try:
            errors = []
            ds_id = state.selected_datasource_id
            sql = state.sql_draft

            if not sql:
                errors.append(PipelineError(
                    node=node_name,
                    message="No SQL to execute.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_SQL
                ))
                return {"errors": errors, "execution": ExecutionModel(row_count=0, rows=[], error="No SQL to execute")}


            if not ds_id:
                errors.append(PipelineError(
                    node=node_name,
                    message="No datasource_id in state.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_DATASOURCE_ID
                ))
                return {
                    "errors": errors,
                    "execution": ExecutionModel(row_count=0, rows=[], error="Missing Datasource ID")
                }

            # 1. Get Adapter/Dialect
            adapter = self.registry.get_adapter(ds_id)
            
            # Use adapter limits (default to safeties if None)
            safeguard_row_limit = adapter.row_limit or 10000
            safeguard_max_bytes = adapter.max_bytes or 10485760 # 10 MB

            try:
                estimate = adapter.cost_estimate(sql)
                if estimate.estimated_rows > safeguard_row_limit:
                    msg = f"Safeguard Triggered: Query estimated to return {estimate.estimated_rows} rows, exceeding limit of {safeguard_row_limit}."
                    logger.warning(msg)

                    errors.append(PipelineError(
                        node=node_name,
                        message=msg,
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SAFEGUARD_VIOLATION
                    ))
                    return {
                        "errors": errors,
                        "execution": ExecutionModel(
                            row_count=0,
                            rows=[],
                            error=f"SAFEGUARD: Too many rows ({estimate.estimated_rows})"
                        ),
                        "reasoning": [{"node": "executor", "content": msg, "type": "warning"}]
                    }
                elif estimate.estimated_cost == -1:
                     logger.warning(f"Estimation failed. Proceeding with caution.")
            except Exception as e:
                logger.warning(f"Safeguard estimation check failed: {e}. Proceeding execution.")

            # 2. Execute via Adapter
            total_bytes = 0
            try:
                sdk_result = adapter.execute(sql)

                rows_as_dicts = [dict(zip(sdk_result.columns, row)) for row in sdk_result.rows]
                
                # SAFEGUARD: Results Size Limit
                total_bytes = sdk_result.bytes_returned or 0
                
                if total_bytes > safeguard_max_bytes:
                    err_msg = (f"Result size ({total_bytes} bytes) exceeds configured limit "
                               f"({safeguard_max_bytes} bytes). Check 'max_bytes' in datasource config.")
                    logger.error(err_msg)
                    return {
                        "errors": [PipelineError(
                            node=node_name,
                            message=err_msg,
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.SAFEGUARD_VIOLATION
                        )],
                        "execution": ExecutionModel(row_count=0, rows=[], error=err_msg)
                    }

                execution_result = ExecutionModel(
                    row_count=sdk_result.row_count,
                    rows=rows_as_dicts,
                    columns=sdk_result.columns,
                    error=None
                )

            except Exception as exc:
                execution_result = ExecutionModel(row_count=0, rows=[], error=str(exc))
                errors.append(PipelineError(
                    node=node_name,
                    message=f"Execution error: {exc}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.DB_EXECUTION_ERROR,
                    stack_trace=str(exc)
                ))

            exec_msg = f"Executed on {ds_id}. Rows: {execution_result.row_count}. Size: {total_bytes}b."
            if execution_result.error:
                exec_msg += f" Error: {execution_result.error}"

            return {
                "execution": execution_result,
                "errors": errors,
                "reasoning": [{"node": "executor", "content": exec_msg}]
            }

        except Exception as exc:
            logger.error(f"Node {node_name} failed: {exc}")
            return {
                "execution": None,
                "errors": [PipelineError(
                    node=node_name, message=f"Executor crash: {exc}",
                    severity=ErrorSeverity.CRITICAL, error_code=ErrorCode.EXECUTOR_CRASH
                )]
            }
