from typing import Dict, Any
from sqlalchemy import create_engine, inspect, text
from nl2sql_adapter_sdk import (
    DryRunResult,
    QueryPlan,
    CostEstimate,
    ForeignKey
)
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class PostgresAdapter(BaseSQLAlchemyAdapter):




    def dry_run(self, sql: str) -> DryRunResult:
        try:
            self.execute(f"EXPLAIN {sql}")
            return DryRunResult(is_valid=True)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, sql: str) -> QueryPlan:
        try:
            res = self.execute(f"EXPLAIN (FORMAT JSON) {sql}")
            return QueryPlan(plan_text=str(res.rows))
        except Exception:            # Fallback
            return QueryPlan(plan_text="Could not retrieve plan")

    def cost_estimate(self, sql: str) -> CostEstimate:
        try:
            res = self.execute(f"EXPLAIN (FORMAT JSON) {sql}")
            if res.rows and res.rows[0]:
                plan_data = res.rows[0][0] # The JSON object/list
                if isinstance(plan_data, list) and len(plan_data) > 0:
                    root = plan_data[0].get('Plan', {})
                    return CostEstimate(
                        estimated_cost=float(root.get('Total Cost', 0.0)),
                        estimated_rows=int(root.get('Plan Rows', 0))
                    )
            return CostEstimate(estimated_cost=0.0, estimated_rows=0)
        except Exception:
            return CostEstimate(estimated_cost=0.0, estimated_rows=0)


