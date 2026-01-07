from typing import Any, List
from sqlalchemy import create_engine, text, inspect
from nl2sql_adapter_sdk import (
    QueryResult, 
    CostEstimate,
    DryRunResult,
    QueryPlan
)
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

class MysqlAdapter(BaseSQLAlchemyAdapter):

    def connect(self) -> None:
        """MySQL-specific connection with Native Server-Side Timeout."""
        if not self.connection_string:
             raise ValueError(f"Connection string is required for {self}")
             
        connect_args = {}
        if self.statement_timeout_ms:
            # Native MySQL Timeout (server-side)
            # SET MAX_EXECUTION_TIME={ms}
            connect_args["init_command"] = f"SET MAX_EXECUTION_TIME={self.statement_timeout_ms}"

        try:
            self.engine = create_engine(
                self.connection_string, 
                pool_pre_ping=True,
                execution_options=self.execution_options,
                connect_args=connect_args
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to connect to MySQL: {e}")
            raise

    def dry_run(self, sql: str) -> DryRunResult:
        try:
            with self.engine.connect() as conn:
                trans = conn.begin()
                conn.execute(text(sql))
                trans.rollback()
            return DryRunResult(is_valid=True)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, sql: str) -> QueryPlan:
        try:
            with self.engine.connect() as conn:
                res = conn.execute(text(f"EXPLAIN FORMAT=JSON {sql}")).scalar()
                return QueryPlan(plan_text=str(res))
        except Exception as e:
            return QueryPlan(plan_text=f"Error: {e}")





    def cost_estimate(self, sql: str) -> CostEstimate:
        import json
        try:
            with self.engine.connect() as conn:
                res = conn.execute(text(f"EXPLAIN FORMAT=JSON {sql}")).scalar()
                if res:
                    data = json.loads(res)
                    # structure: { "query_block": { "cost_info": { "query_cost": "1.00" } } }
                    cost_info = data.get('query_block', {}).get('cost_info', {})
                    return CostEstimate(
                        estimated_cost=float(cost_info.get('query_cost', 0.0)),
                        estimated_rows=0 # MySQL doesn't give a single total rows estimate easily
                    )
        except Exception:
             pass
        return CostEstimate(estimated_cost=0.0, estimated_rows=0)
