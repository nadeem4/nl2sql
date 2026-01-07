from typing import Any, List, Dict
from sqlalchemy import create_engine, text, inspect
from nl2sql_adapter_sdk import (
 
    QueryResult, 
    CostEstimate,
    DryRunResult,
    QueryPlan
)
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class SqliteAdapter(BaseSQLAlchemyAdapter):

    def construct_uri(self, args: Dict[str, Any]) -> str:
        database = args.get("database", ":memory:")
        return f"sqlite:///{database}"

    def connect(self) -> None:
        """Sqlite-specific connection with Locking Timeout."""
        if not self.connection_string:
             raise ValueError(f"Connection string is required for {self}")
             
        connect_args = {}
        if self.statement_timeout_ms:
            # SQLite 'timeout' is for waiting for the lock, not execution duration.
            # But it's the closest/best we can do for "timeout".
            connect_args["timeout"] = self.statement_timeout_ms / 1000.0

        try:
            self.engine = create_engine(
                self.connection_string, 
                pool_pre_ping=True, # Less relevant for sqlite but harmless
                execution_options=self.execution_options,
                connect_args=connect_args
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to connect to Sqlite: {e}")
            raise
    def dry_run(self, query: str) -> DryRunResult:
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"EXPLAIN QUERY PLAN {query}"))
            return DryRunResult(valid=True, error=None)
        except Exception as e:
            return DryRunResult(valid=False, error=str(e))

    def explain(self, query: str) -> QueryPlan:
         return QueryPlan(original_query=query, plan="EXPLAIN QUERY PLAN not fully parsed")


    
    def cost_estimate(self, query: str) -> CostEstimate:
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"EXPLAIN QUERY PLAN {query}"))
            return CostEstimate(estimated_cost=1.0, estimated_rows=10) # Stub
        except Exception:
            return CostEstimate(estimated_cost=-1.0, estimated_rows=0)
