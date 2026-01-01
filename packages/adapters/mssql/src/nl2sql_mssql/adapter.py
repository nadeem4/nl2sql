from typing import Any, List
from sqlalchemy import create_engine, text, inspect
from nl2sql_adapter_sdk import (
 
    QueryResult, 
    CostEstimate,
    DryRunResult,
    QueryPlan
)
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class MssqlAdapter(BaseSQLAlchemyAdapter):

    def dry_run(self, sql: str) -> DryRunResult:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SET NOEXEC ON"))
                try:
                    conn.execute(text(sql))
                    valid = True
                    msg = None
                except Exception as e:
                    valid = False
                    msg = str(e)
                finally:
                    conn.execute(text("SET NOEXEC OFF"))
            return DryRunResult(is_valid=valid, error_message=msg)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, sql: str) -> QueryPlan:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SET SHOWPLAN_XML ON"))
                res = conn.execute(text(sql)).fetchall()
                conn.execute(text("SET SHOWPLAN_XML OFF"))
                return QueryPlan(plan_text="\n".join([str(r[0]) for r in res]))
        except Exception as e:
            return QueryPlan(plan_text=f"Error: {e}")





    def cost_estimate(self, sql: str) -> CostEstimate:
        import re
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SET SHOWPLAN_XML ON"))
                try:
                    res = conn.execute(text(sql)).fetchone()
                finally:
                    conn.execute(text("SET SHOWPLAN_XML OFF"))
                
                if res and res[0]:
                    xml_str = res[0]
                    # Extract cost and rows using regex to avoid namespace complexity
                    # StatementSubTreeCost="0.00328" StatementEstRows="1"
                    cost_match = re.search(r'StatementSubTreeCost="([^"]+)"', xml_str)
                    rows_match = re.search(r'StatementEstRows="([^"]+)"', xml_str)
                    
                    return CostEstimate(
                        estimated_cost=float(cost_match.group(1)) if cost_match else 0.0,
                        estimated_rows=float(rows_match.group(1)) if rows_match else 0
                    )
        except Exception:
             pass
        return CostEstimate(estimated_cost=0.0, estimated_rows=0)

