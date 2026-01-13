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



def _execute_in_process(
    engine_type: str,
    ds_id: str,
    connection_args: Dict[str, Any],
    sql: str,
    timeout_ms: int = None,
    row_limit: int = None,
    max_bytes: int = None
) -> SdkExecutionResult:
    """Executes SQL in a separate process to isolate crashes (segfaults).
    
    This function re-instantiates the adapter inside the worker process to avoid 
    pickling issues with database connections.

    Args:
        engine_type (str): The type of datasource engine (e.g., 'postgres', 'sqlite').
        ds_id (str): The unique identifier of the datasource.
        connection_args (Dict[str, Any]): Arguments required to establish a DB connection.
        sql (str): The SQL query string to execute.
        timeout_ms (int, optional): Statement timeout in milliseconds.
        row_limit (int, optional): Maximum number of rows to return.
        max_bytes (int, optional): Maximum size of the result in bytes.

    Returns:
        SdkExecutionResult: The query execution result containing rows and columns.

    Raises:
        ValueError: If the engine type is unknown.
        Exception: Propagates any exception raised by the adapter execution.
    """
    available = discover_adapters()
    if engine_type not in available:
        raise ValueError(f"Unknown datasource engine type: {engine_type}")
    
    adapter_cls = available[engine_type]
    
    adapter = adapter_cls(
        datasource_id=ds_id,
        datasource_engine_type=engine_type,
        connection_args=connection_args,
        statement_timeout_ms=timeout_ms,
        row_limit=row_limit,
        max_bytes=max_bytes
    )
    
    return adapter.execute(sql)


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

            adapter = self.registry.get_adapter(ds_id)
            
            safeguard_row_limit = adapter.row_limit or 10000
            safeguard_max_bytes = adapter.max_bytes or 10485760 # 10 MB

            total_bytes = 0
            execution_future = None
            try:
                pool = get_execution_pool()
                
                execution_future = pool.submit(
                    _execute_in_process,
                    engine_type=adapter.datasource_engine_type,
                    ds_id=ds_id,
                    connection_args=adapter.connection_args,
                    sql=sql,
                    timeout_ms=adapter.statement_timeout_ms,
                    row_limit=adapter.row_limit,
                    max_bytes=adapter.max_bytes
                )
                
                sdk_result = execution_future.result()

                rows_as_dicts = [dict(zip(sdk_result.columns, row)) for row in sdk_result.rows]
                
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
                error_msg = str(exc)
                code = ErrorCode.DB_EXECUTION_ERROR
                
                is_crash = False
                try:
                    from concurrent.futures.process import BrokenProcessPool
                    if isinstance(exc, BrokenProcessPool):
                        is_crash = True
                except ImportError:
                    pass
                
                if not is_crash and ("BrokenProcessPool" in error_msg or "Terminated" in error_msg):
                    is_crash = True

                if is_crash:
                    error_msg = f"SANDBOX CRASH: The worker process died while executing the query. This indicates a driver segfault or OOM. ({exc})"
                    code = ErrorCode.EXECUTOR_CRASH
                    severity = ErrorSeverity.CRITICAL
                else:
                    severity = ErrorSeverity.ERROR

                execution_result = ExecutionModel(row_count=0, rows=[], error=error_msg)
                errors.append(PipelineError(
                    node=node_name,
                    message=f"Execution error: {error_msg}",
                    severity=severity,
                    error_code=code,
                    stack_trace=str(exc)
                ))

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
