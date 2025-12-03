from __future__ import annotations

from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.engine_factory import run_read_query
from nl2sql.schemas import GraphState
from nl2sql.security import enforce_read_only
from nl2sql.tracing import span


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

    def __call__(self, state: GraphState) -> GraphState:
        """
        Executes the execution step.

        Args:
            state: The current graph state.

        Returns:
            The updated graph state with execution results.
        """
        if not state.sql_draft:
            state.errors.append("No SQL to execute.")
            return state
            
        if not enforce_read_only(state.sql_draft.sql):
            state.errors.append("Security Violation: SQL query contains forbidden keywords (read-only enforcement).")
            state.execution = {"error": "Security Violation"}
            return state
            
        if not state.datasource_id:
            state.errors.append("No datasource_id in state. Router must run before ExecutorNode.")
            state.execution = {"error": "Missing Datasource ID"}
            return state

        profile = self.registry.get_profile(state.datasource_id)
        engine = self.registry.get_engine(state.datasource_id)
        
        with span("executor", {"datasource.id": profile.id, "engine": profile.engine}):
            try:
                rows = run_read_query(engine, state.sql_draft.sql, row_limit=profile.row_limit)
                samples = []
                for row in rows[:3]:
                    try:
                        samples.append(dict(row._mapping))
                    except Exception:
                        samples.append(tuple(row))
                state.execution = {"row_count": len(rows), "sample": samples}
            except Exception as exc:
                state.execution = {"error": str(exc)}
                state.errors.append(f"Execution error: {exc}")
        return state
