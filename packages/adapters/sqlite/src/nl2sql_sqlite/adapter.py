from typing import Any, List
from sqlalchemy import create_engine, text, inspect
from nl2sql_adapter_sdk import (
    CapabilitySet, 
    QueryResult, 
    CostEstimate,
    DryRunResult,
    QueryPlan,
    ExecutionMetrics
)
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class SqliteAdapter(BaseSQLAlchemyAdapter):
    def dry_run(self, query: str) -> DryRunResult:
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"EXPLAIN QUERY PLAN {query}"))
            return DryRunResult(valid=True, error=None)
        except Exception as e:
            return DryRunResult(valid=False, error=str(e))

    def explain(self, query: str) -> QueryPlan:
         return QueryPlan(original_query=query, plan="EXPLAIN QUERY PLAN not fully parsed")

    def metrics(self) -> ExecutionMetrics:
        return ExecutionMetrics(execution_time_ms=0.0, rows_returned=0)

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            supports_cte=True,
            supports_window_functions=True,
            supports_limit_offset=True,
            supports_multi_db_join=True,
            supports_dry_run=False
        )
    
    # fetch_schema, execute are handled by BaseSQLAlchemyAdapter

    def cost_estimate(self, query: str) -> CostEstimate:
        # EXPLAIN QUERY PLAN is available but parsing is complex.
        # Check if EXPLAIN is available
        try:
             with self.engine.connect() as conn:
                 conn.execute(text(f"EXPLAIN QUERY PLAN {query}"))
             # If successful, at least syntax is valid
             return CostEstimate(estimated_cost=1.0, estimated_rows=10) # Stub
        except Exception:
             return CostEstimate(estimated_cost=-1.0, estimated_rows=0)
