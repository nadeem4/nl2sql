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

class MysqlAdapter(BaseSQLAlchemyAdapter):
    def dry_run(self, query: str) -> DryRunResult:
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"EXPLAIN {query}"))
            return DryRunResult(valid=True, error=None)
        except Exception as e:
            return DryRunResult(valid=False, error=str(e))

    def explain(self, query: str) -> QueryPlan:
         try:
             with self.engine.connect() as conn:
                 # MySQL JSON format
                 res = conn.execute(text(f"EXPLAIN FORMAT=JSON {query}"))
                 return QueryPlan(original_query=query, plan=str(res.fetchone()[0]))
         except Exception:
             return QueryPlan(original_query=query, plan="Could not retrieve plan")

    def metrics(self) -> ExecutionMetrics:
        return ExecutionMetrics(execution_time_ms=0.0, rows_returned=0)

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            supports_cte=True,
            supports_window_functions=True,
            supports_limit_offset=True,
            supports_multi_db_join=False,
            supports_dry_run=False
        )

    def cost_estimate(self, query: str) -> CostEstimate:
        # EXPLAIN FORMAT=JSON could work for MySQL
        return CostEstimate(estimated_cost=10.0, estimated_rows=100) # Stub
