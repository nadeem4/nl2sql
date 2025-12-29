from typing import Dict, Any
from sqlalchemy import create_engine, inspect, text
from nl2sql_adapter_sdk import (
    CapabilitySet,
    DryRunResult,
    QueryPlan,
    CostEstimate,
    ExecutionMetrics,
    ForeignKey
)
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class PostgresAdapter(BaseSQLAlchemyAdapter):


    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            supports_cte=True,
            supports_window_functions=True,
            supports_limit_offset=True,
            supports_dry_run=True  # via EXPLAIN
        )

    def dry_run(self, sql: str) -> DryRunResult:
        # Simple dry run via EXPLAIN
        try:
            self.execute(f"EXPLAIN {sql}")
            return DryRunResult(is_valid=True)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, sql: str) -> QueryPlan:
        try:
            res = self.execute(f"EXPLAIN (FORMAT JSON) {sql}")
            return QueryPlan(plan_text=str(res.rows))
        except Exception:
            # Fallback
            return QueryPlan(plan_text="Could not retrieve plan")

    def cost_estimate(self, sql: str) -> CostEstimate:
        return CostEstimate(estimated_cost=0.0, estimated_rows=0)

    def metrics(self) -> ExecutionMetrics:
        return ExecutionMetrics(execution_ms=0, rows_returned=0, engine="postgres")
