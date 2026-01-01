from typing import Any, List
from sqlalchemy import create_engine, text, inspect
from nl2sql_adapter_sdk import (
 
    QueryResult, 
    CostEstimate,
    DryRunResult,
    QueryPlan
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


    
    def cost_estimate(self, query: str) -> CostEstimate:
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"EXPLAIN QUERY PLAN {query}"))
            return CostEstimate(estimated_cost=1.0, estimated_rows=10) # Stub
        except Exception:
            return CostEstimate(estimated_cost=-1.0, estimated_rows=0)
