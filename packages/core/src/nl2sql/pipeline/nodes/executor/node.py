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
            capabilities = adapter.capabilities()
            profile = self.registry.get_profile(ds_id)

            # 1a. Security Check (Core Layer Defense)
            # Default to generic if dialect not specified
            dialect = profile.engine if profile and profile.engine else "generic"
            
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
            
            if hasattr(capabilities, "supports_cost_estimation") or True: # assume true/check
                try:
                    estimate = adapter.cost_estimate(sql)
                    if estimate.estimated_rows > SAFEGUARD_ROW_LIMIT:
                        msg = f"Safeguard Triggered: Query estimated to return {estimate.estimated_rows} rows, exceeding limit of {SAFEGUARD_ROW_LIMIT}."
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
                    elif estimate.estimated_cost == -1: # or some other check
                        logger.warning(f"Estimation failed. Proceeding with caution.")
                        
                except Exception as e:
                    logger.warning(f"Safeguard estimation check failed: {e}. Proceeding execution.")

            # 2. Execute via Adapter
            try:
                sdk_result = adapter.execute(sql)
                
                # 3. Map SDK Result -> Graph Result
                # Convert List[List] to List[Dict]
                rows_as_dicts = [dict(zip(sdk_result.columns, row)) for row in sdk_result.rows]
                
                execution_result = ExecutionModel(
                    row_count=sdk_result.row_count,
                    rows=rows_as_dicts,
                    columns=sdk_result.columns,
                    error=None 
                )
                
                # if sdk_result.error:
                #      # Log the error but don't crash pipeline, let Aggregator handle
                #      logger.warning(f"Execution Error: {sdk_result.error}")
                     
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
