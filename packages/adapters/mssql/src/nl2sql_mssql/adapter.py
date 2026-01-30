from typing import Any, List, Dict
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.dialects import mssql
from nl2sql_sqlalchemy_adapter import (
    CostEstimate,
    DryRunResult,
    QueryPlan,
)
from nl2sql_sqlalchemy_adapter import BaseSQLAlchemyAdapter

from pydantic import BaseModel, Field, SecretStr
from typing import Optional

class MssqlConnectionConfig(BaseModel):
    """Strict configuration schema for MSSQL adapter."""
    type: str = Field("mssql", description="Connection type")
    host: str = Field(..., description="Server hostname")
    user: Optional[str] = None
    password: Optional[SecretStr] = None
    port: int = 1433
    database: str = Field(..., description="Database name")
    driver: str = "ODBC Driver 17 for SQL Server"
    trusted_connection: bool = False
    options: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"extra": "ignore"}

class MssqlAdapter(BaseSQLAlchemyAdapter):

    def construct_uri(self, args: Dict[str, Any]) -> str:
        """Constructs the MSSQL connection URI.

        Args:
            args: The raw connection arguments dictionary.

        Returns:
            str: The fully constructed SQLAlchemy connection URI.
        
        Raises:
            ValidationError: If the configuration is invalid.
        """
        config = MssqlConnectionConfig(**args)
        
        user = config.user or ""
        password = config.password.get_secret_value() if config.password else ""
        host = config.host
        port = config.port
        database = config.database
        driver = config.driver
        
        options = config.options.copy()
        options["driver"] = driver
        
        if config.trusted_connection:
            options["Trusted_Connection"] = "yes"
            
        creds = f"{user}:{password}@" if user or password else ""
        netloc = f"{host}:{port}"
        
        from urllib.parse import urlencode
        query_str = "?" + urlencode(options)
        
        return f"mssql+pyodbc://{creds}{netloc}/{database}{query_str}"

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
        except Exception as e:
            return QueryPlan(plan_text=f"Error: {e}")

    def get_dialect(self) -> str:
        """MSSQL uses T-SQL dialect."""
        return mssql.dialect.name

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

    
    @property
    def exclude_schemas(self) -> set[str]:
        return {"sys", "INFORMATION_SCHEMA"}

