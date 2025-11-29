from __future__ import annotations

from nl2sql.datasource_config import DatasourceProfile
from nl2sql.engine_factory import make_engine, run_read_query
from nl2sql.schemas import GraphState
from nl2sql.tracing import span


class ExecutorNode:
    def __init__(self, profile: DatasourceProfile):
        self.profile = profile

    def __call__(self, state: GraphState) -> GraphState:
        if not state.sql_draft:
            state.errors.append("No SQL to execute.")
            return state
            
        engine = make_engine(self.profile)
        with span("executor", {"datasource.id": self.profile.id, "engine": self.profile.engine}):
            try:
                rows = run_read_query(engine, state.sql_draft["sql"], row_limit=self.profile.row_limit)
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
