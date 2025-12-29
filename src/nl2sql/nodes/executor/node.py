from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.adapter_sdk import ExecutionResult as SdkExecutionResult

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import ExecutionModel
from nl2sql.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.security import enforce_read_only
from nl2sql.logger import get_logger

logger = get_logger("executor")

class ExecutorNode:
    """
    Executes the generated SQL query via the Datasource Adapter.
    """

    def __init__(self, registry: DatasourceRegistry):
        self.registry = registry

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = "executor"

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

            # 1. Get Adapter
            adapter = self.registry.get_adapter(ds_id)
            capabilities = adapter.get_capabilities()

            # 1a. Security Check (Core Layer Defense)
            # Default to generic if dialect not specified
            dialect = capabilities.supported_dialects[0] if capabilities.supported_dialects else "generic"
            
            if not enforce_read_only(sql, dialect=dialect):
                errors.append(PipelineError(
                    node=node_name,
                    message="Security Violation: SQL query contains forbidden keywords.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION
                ))
                return {
                     "errors": errors,
                     "execution": ExecutionModel(row_count=0, rows=[], error="Security Violation")
                 }

            # 1b. Pre-flight Safeguard (Data Flood Protection)
            # Prevent aggregator OOM by rejecting massive result sets
            SAFEGUARD_ROW_LIMIT = 10000 
            
            if capabilities.supports_hueristic_estimation:
                try:
                    estimate = adapter.estimate(sql)
                    if estimate.will_succeed and estimate.estimated_row_count > SAFEGUARD_ROW_LIMIT:
                        msg = f"Safeguard Triggered: Query estimated to return {estimate.estimated_row_count} rows, exceeding limit of {SAFEGUARD_ROW_LIMIT}."
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
                                error=f"SAFEGUARD: Too many rows ({estimate.estimated_row_count})"
                            ),
                            "reasoning": [{"node": "executor", "content": msg, "type": "warning"}]
                        }
                    elif not estimate.will_succeed:
                        logger.warning(f"Estimation failed: {estimate.reason}. Proceeding with caution.")
                        
                except Exception as e:
                    logger.warning(f"Safeguard estimation check failed: {e}. Proceeding execution.")

            # 2. Execute via Adapter
            try:
                sdk_result = adapter.execute(sql)
                
                # 3. Map SDK Result -> Graph Result
                execution_result = ExecutionModel(
                    row_count=sdk_result.row_count,
                    rows=sdk_result.rows,
                    columns=sdk_result.column_names,
                    error=sdk_result.error
                )
                
                if sdk_result.error:
                     # Log the error but don't crash pipeline, let Aggregator handle
                     logger.warning(f"Execution Error: {sdk_result.error}")
                     
            except Exception as exc:
                execution_result = ExecutionModel(row_count=0, rows=[], error=str(exc))
                errors.append(PipelineError(
                    node=node_name,
                    message=f"Execution error: {exc}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.DB_EXECUTION_ERROR,
                    stack_trace=str(exc)
                ))
            
            exec_msg = f"Executed on {ds_id}. Rows: {execution_result.row_count}."
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
