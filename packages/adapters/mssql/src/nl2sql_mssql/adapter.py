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

class MssqlAdapter(BaseSQLAlchemyAdapter):
    def dry_run(self, query: str) -> DryRunResult:
        # MSSQL SET NOEXEC ON is tricky with SQLAlchemy connection pooling
        # usage simple parse-check if possible or assumption
        return DryRunResult(valid=True, error=None)

    def explain(self, query: str) -> QueryPlan:
         # SHOWPLAN_XML
         return QueryPlan(original_query=query, plan="Not implemented")

    def metrics(self) -> ExecutionMetrics:
        return ExecutionMetrics(execution_time_ms=0.0, rows_returned=0)

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            supports_cte=True,
            supports_window_functions=True,
            supports_limit_offset=False,
            supports_multi_db_join=False,
            supports_dry_run=False
        )

    def cost_estimate(self, query: str) -> CostEstimate:
        # Basic safeguard for now
        return CostEstimate(estimated_cost=10.0, estimated_rows=100) # Stub
