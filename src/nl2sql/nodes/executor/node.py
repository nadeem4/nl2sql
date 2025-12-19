from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.engine_factory import run_read_query
from .schemas import ExecutionModel

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState
from nl2sql.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.security import enforce_read_only
from nl2sql.logger import get_logger

logger = get_logger("executor")

class ExecutorNode:
    """
    Executes the generated SQL query against the target database.

    Enforces read-only security checks before execution.
    """

    def __init__(self, registry: DatasourceRegistry):
        """
        Initializes the ExecutorNode.

        Args:
            registry: Datasource registry for accessing profiles and engines.
        """
        self.registry = registry

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """
        Executes the execution step.

        Args:
            state: The current graph state.

        Returns:
            Dictionary updates for the graph state with execution results.
        """
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
                    message="No datasource_id in state. Router must run before ExecutorNode.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.MISSING_DATASOURCE_ID
                ))
                return {
                    "errors": errors,
                    "execution": ExecutionModel(row_count=0, rows=[], error="Missing Datasource ID")
                }

            profile = self.registry.get_profile(ds_id)
            
            dialect = None
            if "mssql" in profile.engine:
                dialect = "tsql"
            elif "postgres" in profile.engine:
                dialect = "postgres"
            elif "mysql" in profile.engine:
                dialect = "mysql"
            elif "sqlite" in profile.engine:
                dialect = "sqlite"
            elif "oracle" in profile.engine:
                dialect = "oracle"

            if not enforce_read_only(state.sql_draft, dialect=dialect):
                errors.append(PipelineError(
                    node=node_name,
                    message="Security Violation: SQL query contains forbidden keywords (read-only enforcement).",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION
                ))
                return {
                    "errors": errors,
                    "execution": ExecutionModel(row_count=0, rows=[], error="Security Violation")
                }
                
            profile = self.registry.get_profile(ds_id)
            engine = self.registry.get_engine(ds_id)
            
            execution_result = None
            
            try:
                rows = run_read_query(engine, sql)
                
                result_rows = []
                columns = []
                if rows:
                    columns = list(rows[0]._mapping.keys())
                    for row in rows:
                        result_rows.append(dict(row._mapping))
                
                execution_result = ExecutionModel(
                    row_count=len(rows),
                    rows=result_rows,
                    columns=columns
                )
            except Exception as exc:
                execution_result = ExecutionModel(
                    row_count=0,
                    rows=[],
                    error=str(exc)
                )
                errors.append(PipelineError(
                    node=node_name,
                    message=f"Execution error: {exc}",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.DB_EXECUTION_ERROR,
                    stack_trace=str(exc)
                ))
            
            exec_msg = f"Executed SQL on {ds_id}. Rows returned: {execution_result.row_count}."
            if execution_result.error:
                 exec_msg += f" Error: {execution_result.error}"
            
            return {
                "execution": execution_result,
                "errors": errors,
                "reasoning": [{"node": "executor", "content": exec_msg}]
            }

        except Exception as exc:
            logger.error(f"Node {node_name} failed: {exc}")
            err = PipelineError(
                node=node_name,
                message=f"Executor failed: {exc}",
                severity=ErrorSeverity.CRITICAL,
                error_code=ErrorCode.EXECUTOR_CRASH,
                stack_trace=str(exc)
            )
            return {
                "execution": None,
                "errors": [err],
                "reasoning": [{"node": "executor", "content": f"Execution exception: {exc}", "type": "error"}]
            }
