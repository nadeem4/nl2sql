from typing import Any, List, Dict
from sqlalchemy import URL, create_engine, text, inspect
from sqlalchemy.dialects import mysql
from nl2sql.adapters.sqlalchemy_base import (
    CostEstimate,
    DryRunResult,
    QueryPlan,
    BaseSQLAlchemyAdapter
)

from pydantic import BaseModel, Field, SecretStr
from typing import Optional

class MysqlConnectionConfig(BaseModel):
    """Strict configuration schema for MySQL adapter."""
    host: str = Field(..., description="MySQL server hostname")
    user: str = Field(..., description="Username")
    password: SecretStr = Field(..., description="Password")
    port: int = 3306
    database: str = Field(..., description="Database name")
    options: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"extra": "ignore"}

class MysqlAdapter(BaseSQLAlchemyAdapter):
    interface_version = 1
    driver_modules = ("pymysql",)
    install_hint = 'pip install "nl2sql-engine[mysql]"'

    def construct_uri(self, args: Dict[str, Any]) -> str:
        """Constructs the MySQL connection URI.

        Args:
            args: The raw connection arguments dictionary.

        Returns:
            str: The fully constructed SQLAlchemy connection URI.
        
        Raises:
            ValidationError: If the configuration is invalid.
        """
        config = MysqlConnectionConfig(**args)
        
        # URL.create escapes the credentials, so a password holding @ : / # %
        # stays a password instead of moving the host or port boundary.
        return URL.create(
            "mysql+pymysql",
            username=config.user or None,
            password=config.password.get_secret_value() or None,
            host=config.host,
            port=config.port,
            database=config.database,
            query={key: str(value) for key, value in config.options.items()},
        ).render_as_string(hide_password=False)

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
                    cost_info = data.get('query_block', {}).get('cost_info', {})
                    return CostEstimate(
                        estimated_cost=float(cost_info.get('query_cost', 0.0)),
                        estimated_rows=0 # MySQL doesn't give a single total rows estimate easily
                    )
        except Exception:
             pass
        return CostEstimate(estimated_cost=0.0, estimated_rows=0)

    def get_dialect(self) -> str:
        return mysql.dialect.name


    @property
    def exclude_schemas(self) -> set[str]:
        return {"mysql", "INFORMATION_SCHEMA", "performance_schema", "sys"}
