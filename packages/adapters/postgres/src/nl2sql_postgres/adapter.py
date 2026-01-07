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

    def construct_uri(self, args: Dict[str, Any]) -> str:
        user = args.get("user", "")
        password = args.get("password", "")
        host = args.get("host", "localhost")
        port = args.get("port", "")
        database = args.get("database", "")
        
        options = args.get("options", {}).copy()
        
        creds = f"{user}:{password}@" if user or password else ""
        netloc = f"{host}:{port}" if port else host
        
        query_str = ""
        if options:
            from urllib.parse import urlencode
            query_str = "?" + urlencode(options)
            
        return f"postgresql://{creds}{netloc}/{database}{query_str}"

    def connect(self) -> None:
        """Postgres-specific connection with Native Server-Side Timeout."""
        if not self.connection_string:
             raise ValueError(f"Connection string is required for {self}")
             
        connect_args = {}
        if self.statement_timeout_ms:
            # Native Postgres Timeout (server-side)
            # -c statement_timeout={ms}
            connect_args["options"] = f"-c statement_timeout={self.statement_timeout_ms}"

        try:
            self.engine = create_engine(
                self.connection_string, 
                pool_pre_ping=True,
                execution_options=self.execution_options, # Pass standard options too
                connect_args=connect_args
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to connect to Postgres: {e}")
            raise




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


