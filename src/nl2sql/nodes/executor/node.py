from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Any
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.engine_factory import run_read_query
from .schemas import ExecutionModel

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState
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
            if not state.sql_draft:
                errors.append("No SQL to execute.")
                return {"errors": errors}
                
            if not state.datasource_id:
                errors.append("No datasource_id in state. Router must run before ExecutorNode.")
                return {
                    "errors": errors,
                    "execution": ExecutionModel(row_count=0, rows=[], error="Missing Datasource ID")
                }

            # Handle Set[str] -> Pick sorted first for deterministic execution
            ds_ids = state.datasource_id if isinstance(state.datasource_id, (set, list)) else {state.datasource_id}
            if not ds_ids:
                # Should be caught by check above but for type safety
                errors.append("Empty datasource_id set.")
                return {"errors": errors}
                
            target_ds_id = sorted(list(ds_ids))[0]
            
            if len(ds_ids) > 1:
                errors.append(f"Warning: Multiple datasources selected {ds_ids}, executing on primary: {target_ds_id}")

            profile = self.registry.get_profile(target_ds_id)
            
            # Map SQLAlchemy engine to sqlglot dialect
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
                errors.append("Security Violation: SQL query contains forbidden keywords (read-only enforcement).")
                return {
                    "errors": errors,
                    "execution": ExecutionModel(row_count=0, rows=[], error="Security Violation")
                }
                
            profile = self.registry.get_profile(target_ds_id)
            engine = self.registry.get_engine(target_ds_id)
            
            execution_result = None
            
            try:
                # Remove hardcoded limit, rely on profile.row_limit
                rows = run_read_query(engine, state.sql_draft)
                
                # Convert rows to list of dicts
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
                errors.append(f"Execution error: {exc}")
            
            exec_msg = f"Executed SQL on {target_ds_id}. Rows returned: {execution_result.row_count}."
            if execution_result.error:
                 exec_msg += f" Error: {execution_result.error}"
            
            return {
                "execution": execution_result,
                "errors": errors,
                "reasoning": {"executor": [exec_msg]}
            }

        except Exception as exc:
            logger.error(f"Node {node_name} failed: {exc}")
            return {
                "execution": None,
                "errors": [f"Executor failed: {exc}"],
                "reasoning": {"executor": [f"Execution exception: {exc}"]}
            }
