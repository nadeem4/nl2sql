from typing import Dict, Any
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from nl2sql_sqlalchemy_adapter import (
    DryRunResult,
    QueryPlan,
    CostEstimate,
    BaseSQLAlchemyAdapter
)

from pydantic import BaseModel, Field, SecretStr
from typing import Optional

class PostgresConnectionConfig(BaseModel):
    """Strict configuration schema for Postgres adapter."""
    type: str = Field("postgresql", description="Connection type")
    host: str = Field(..., description="Postgres server hostname")
    user: str = Field(..., description="Username")
    password: SecretStr = Field(..., description="Password")
    port: int = 5432
    database: str = Field(..., description="Database name")
    options: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"extra": "ignore"}

class PostgresAdapter(BaseSQLAlchemyAdapter):

    def construct_uri(self, args: Dict[str, Any]) -> str:
        """Constructs the Postgres connection URI.

        Args:
            args: The raw connection arguments dictionary.

        Returns:
            str: The fully constructed SQLAlchemy connection URI.
        
        Raises:
            ValidationError: If the configuration is invalid.
        """
        config = PostgresConnectionConfig(**args)
        
        user = config.user
        password = config.password.get_secret_value()
        host = config.host
        port = config.port
        database = config.database
        options = config.options.copy()
        
        creds = f"{user}:{password}@" if user or password else ""
        netloc = f"{host}:{port}"
        
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

    def get_dialect(self) -> str:
        return postgresql.dialect.name


    def exclude_schemas(self) -> set[str]:
        return {"pg_catalog", "information_schema"}