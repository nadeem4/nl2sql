from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING, Dict, Any

from nl2sql.datasources import DatasourceRegistry
from nl2sql.datasources.discovery import discover_adapters
from nl2sql_adapter_sdk.contracts import AdapterRequest, ResultFrame

if TYPE_CHECKING:
    from nl2sql.pipeline.state import SubgraphExecutionState

from .schemas import ExecutionModel, ExecutorResponse
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.common.sandbox import get_execution_pool
from nl2sql.context import NL2SQLContext

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
        
        adapter_request = AdapterRequest(
            plan_type="sql",
            payload={"sql": request.sql},
            limits=request.limits,
        )
        sdk_result = adapter.execute(adapter_request)

        if isinstance(sdk_result, ResultFrame):
            if not sdk_result.success:
                err_msg = sdk_result.error.safe_message if sdk_result.error else "Adapter execution failed."
                return ExecutionResult(success=False, error=err_msg)

            rows_as_dicts = sdk_result.to_row_dicts()
            columns = [col.name for col in sdk_result.columns]
            row_count = sdk_result.row_count or len(rows_as_dicts)
            bytes_returned = sdk_result.bytes or 0
        else:
            # Backwards compatibility: adapter returned legacy object
            rows_as_dicts = [dict(zip(sdk_result.columns, row)) for row in sdk_result.rows]
            columns = sdk_result.columns
            row_count = sdk_result.row_count
            bytes_returned = sdk_result.bytes_returned
        
        duration = (time.perf_counter() - start) * 1000
        
        return ExecutionResult(
            success=True,
            data={
                "row_count": row_count,
                "rows": rows_as_dicts,
                "columns": columns,
                "bytes_returned": bytes_returned
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

    def __init__(self, ctx: NL2SQLContext):
        """Initializes the ExecutorNode.

        Args:
            registry (DatasourceRegistry): The registry of datasources.
        """
        self.node_name = self.__class__.__name__.lower().replace('node', '')
        self.ds_registry = ctx.ds_registry

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        """Executes the generated SQL against the selected datasource.

        This method validates the input state, retrieves the appropriate adapter 
        configuration, and offloads the SQL execution to a sandboxed process pool. 
        It handles unexpected worker crashes (e.g., Segfaults, OOMs) and enforces 
        configured safeguards on result size.

        Args:
            state (SubgraphExecutionState): The current state of the execution graph.

        Returns:
            Dict[str, Any]: A dictionary containing the 'execution' result,
                any 'errors', and 'reasoning' logs.
        """
        try:
            errors = []
            ds_id = state.sub_query.datasource_id if state.sub_query else None
            sql = state.generator_response.sql_draft if state.generator_response else None

            if not sql:
                errors.append(PipelineError(
                    node=self.node_name,
                    message="No SQL to execute.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_SQL
                ))
                response = ExecutorResponse(
                    execution=ExecutionModel(row_count=0, rows=[], error="No SQL to execute"),
                    errors=errors,
                )
                return {"executor_response": response, "errors": errors}


            if not ds_id:
                errors.append(PipelineError(
                    node=self.node_name,
                    message="No datasource_id in state.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_DATASOURCE_ID
                ))
                response = ExecutorResponse(
                    execution=ExecutionModel(row_count=0, rows=[], error="Missing Datasource ID"),
                    errors=errors,
                )
                return {"executor_response": response, "errors": errors}

            from nl2sql.common.settings import settings
            
            adapter = self.ds_registry.get_adapter(ds_id)
            
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
                error = PipelineError(
                    node=self.node_name,
                    message=msg,
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.SERVICE_UNAVAILABLE,
                )
                response = ExecutorResponse(
                    execution=ExecutionModel(row_count=0, rows=[], error=msg),
                    errors=[error],
                )
                return {"executor_response": response, "errors": [error]}
            except Exception as e:
                # Re-construct failure result if the guard raised (and thus tripped breaker)
                # This handles the "first" failure that trips it, or failures while closed
                
                err_code = ErrorCode.DB_EXECUTION_ERROR
                if isinstance(e, RuntimeError) and "Sandbox Crash" in str(e):
                    err_code = ErrorCode.EXECUTOR_CRASH
                elif isinstance(e, TimeoutError):
                    err_code = ErrorCode.EXECUTION_TIMEOUT

                error = PipelineError(
                    node=self.node_name,
                    message=f"Execution Failed (Breaker Monitored): {e}",
                    severity=ErrorSeverity.ERROR,
                    error_code=err_code,
                )
                response = ExecutorResponse(
                    execution=ExecutionModel(row_count=0, rows=[], error=str(e)),
                    errors=[error],
                )
                return {"executor_response": response, "errors": [error]}

            if not result_contract.success:
                 # Standard logic execution error (Syntax, etc.) that didn't trip breaker
                 error = PipelineError(
                     node=self.node_name,
                     message=f"Execution error: {result_contract.error}",
                     severity=ErrorSeverity.ERROR,
                     error_code=ErrorCode.DB_EXECUTION_ERROR,
                 )
                 response = ExecutorResponse(
                     execution=ExecutionModel(row_count=0, rows=[], error=result_contract.error),
                     errors=[error],
                 )
                 return {"executor_response": response, "errors": [error]}

            result_data = result_contract.data
            total_bytes = result_data.get("bytes_returned", 0)
            
            if total_bytes > safeguard_max_bytes:
                err_msg = (f"Result size ({total_bytes} bytes) exceeds configured limit "
                           f"({safeguard_max_bytes} bytes). Check 'max_bytes' in datasource config.")
                logger.error(err_msg)
                error = PipelineError(
                    node=self.node_name,
                    message=err_msg,
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.SAFEGUARD_VIOLATION,
                )
                response = ExecutorResponse(
                    execution=ExecutionModel(row_count=0, rows=[], error=err_msg),
                    errors=[error],
                )
                return {"executor_response": response, "errors": [error]}

            execution_result = ExecutionModel(
                row_count=result_data["row_count"],
                rows=result_data["rows"],
                columns=result_data["columns"],
                error=None
            )
             


            exec_msg = f"Executed on {ds_id} (Sandbox). Rows: {execution_result.row_count}. Size: {total_bytes}b."
            if execution_result.error:
                exec_msg += f" Error: {execution_result.error}"

            response = ExecutorResponse(
                execution=execution_result,
                errors=errors,
                reasoning=[{"node": self.node_name, "content": exec_msg}],
            )
            return {
                "executor_response": response,
                "errors": errors,
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
                "executor_response": ExecutorResponse(errors=[error]),
                "errors": [error],
            }
