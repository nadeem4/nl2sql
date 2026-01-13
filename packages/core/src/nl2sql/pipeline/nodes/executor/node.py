from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING, Dict, Any

from nl2sql.datasources import DatasourceRegistry
from nl2sql.datasources.discovery import discover_adapters
from nl2sql_adapter_sdk import QueryResult as SdkExecutionResult, DatasourceAdapter

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from .schemas import ExecutionModel
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.common.sandbox import get_execution_pool

logger = get_logger("executor")



from nl2sql.common.contracts import ExecutionRequest, ExecutionResult

def _execute_in_process(request: ExecutionRequest) -> ExecutionResult:
    """Executes SQL in a separate process to isolate crashes (segfaults).
    
    Args:
        request (ExecutionRequest): The execution parameters.

    Returns:
        ExecutionResult: The result of the execution.
    """
    import time
    start = time.perf_counter()
    
    available = discover_adapters()
    if request.engine_type not in available:
        return ExecutionResult(
            success=False, 
            error=f"Unknown datasource engine type: {request.engine_type}"
        )
    
    try:
        adapter_cls = available[request.engine_type]
        
        # Extract safeguards from request.limits
        timeout_ms = request.limits.get("timeout_ms")
        row_limit = request.limits.get("row_limit")
        max_bytes = request.limits.get("max_bytes")

        adapter = adapter_cls(
            datasource_id=request.datasource_id,
            datasource_engine_type=request.engine_type,
            connection_args=request.connection_args,
            statement_timeout_ms=timeout_ms,
            row_limit=row_limit,
            max_bytes=max_bytes
        )
        
        sdk_result = adapter.execute(request.sql)
        
        # Determine success based on adapter behavior (usually raises on error)
        # Assuming adapter.execute returns QueryResult or raises
        
        rows_as_dicts = [dict(zip(sdk_result.columns, row)) for row in sdk_result.rows]
        
        duration = (time.perf_counter() - start) * 1000
        
        return ExecutionResult(
            success=True,
            data={
                "row_count": sdk_result.row_count,
                "rows": rows_as_dicts,
                "columns": sdk_result.columns,
                "bytes_returned": sdk_result.bytes_returned
            },
            metrics={"execution_time_ms": duration}
        )

    except Exception as e:
        return ExecutionResult(success=False, error=str(e))


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

        This method validates the input state, retrieves the appropriate adapter 
        configuration, and offloads the SQL execution to a sandboxed process pool. 
        It handles unexpected worker crashes (e.g., Segfaults, OOMs) and enforces 
        configured safeguards on result size.

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

            from nl2sql.common.settings import settings
            
            adapter = self.registry.get_adapter(ds_id)
            
            safeguard_timeout_ms = adapter.statement_timeout_ms or settings.default_statement_timeout_ms
            safeguard_row_limit = adapter.row_limit or settings.default_row_limit
            safeguard_max_bytes = adapter.max_bytes or settings.default_max_bytes

            total_bytes = 0
            execution_future = None
            pool = get_execution_pool()
            
            request = ExecutionRequest(
                mode="execute",
                datasource_id=ds_id,
                engine_type=adapter.datasource_engine_type,
                connection_args=adapter.connection_args,
                sql=sql,
                limits={
                    "timeout_ms": safeguard_timeout_ms,
                    "row_limit": safeguard_row_limit,
                    "max_bytes": safeguard_max_bytes
                }
            )

            # Use centralized safe execution helper
            from nl2sql.common.sandbox import execute_in_sandbox
            from nl2sql.common.resilience import DB_BREAKER
            import pybreaker

            try:
                @DB_BREAKER
                def _execute_guarded():
                    res = execute_in_sandbox(pool, _execute_in_process, request)
                    if not res.success:
                        # Trip breaker on Crashes (Segfault/OOM) or Timeouts
                        # We do NOT trip on generic SQL errors (Syntax, Constraint)
                        if res.metrics.get("is_crash"):
                            raise RuntimeError(f"Sandbox Crash: {res.error}")
                        if "timed out" in str(res.error).lower():
                            raise TimeoutError(res.error)
                    return res

                result_contract = _execute_guarded()

            except pybreaker.CircuitBreakerError:
                msg = "Execution unavailable (Circuit Breaker Open). The database seems to be down."
                logger.warning(f"DB_BREAKER prevented execution for {ds_id}")
                return {
                        "execution": ExecutionModel(row_count=0, rows=[], error=msg),
                        "errors": [PipelineError(
                            node=node_name,
                            message=msg,
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.SERVICE_UNAVAILABLE
                        )]
                }
            except Exception as e:
                # Re-construct failure result if the guard raised (and thus tripped breaker)
                # This handles the "first" failure that trips it, or failures while closed
                
                err_code = ErrorCode.DB_EXECUTION_ERROR
                if isinstance(e, RuntimeError) and "Sandbox Crash" in str(e):
                    err_code = ErrorCode.EXECUTOR_CRASH
                elif isinstance(e, TimeoutError):
                    err_code = ErrorCode.EXECUTION_TIMEOUT

                return {
                    "execution": ExecutionModel(row_count=0, rows=[], error=str(e)),
                    "errors": [PipelineError(
                        node=node_name,
                        message=f"Execution Failed (Breaker Monitored): {e}",
                        severity=ErrorSeverity.ERROR, 
                        error_code=err_code
                    )]
                }

            if not result_contract.success:
                 # Standard logic execution error (Syntax, etc.) that didn't trip breaker
                 return {
                    "execution": ExecutionModel(row_count=0, rows=[], error=result_contract.error),
                    "errors": [PipelineError(
                        node=node_name,
                        message=f"Execution error: {result_contract.error}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.DB_EXECUTION_ERROR
                    )]
                 }

            result_data = result_contract.data
            total_bytes = result_data.get("bytes_returned", 0)
            
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
                row_count=result_data["row_count"],
                rows=result_data["rows"],
                columns=result_data["columns"],
                error=None
            )
             


            exec_msg = f"Executed on {ds_id} (Sandbox). Rows: {execution_result.row_count}. Size: {total_bytes}b."
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
