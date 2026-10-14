import math
from typing import Any, List, Dict
from sqlalchemy import URL, create_engine, event, text, inspect
from nl2sql.adapters.sqlalchemy_base import (
    CostEstimate,
    DryRunResult,
    QueryPlan,
)
from nl2sql.adapters.sqlalchemy_base import BaseSQLAlchemyAdapter

from pydantic import BaseModel, Field, SecretStr
from typing import Optional

class MssqlConnectionConfig(BaseModel):
    """Strict configuration schema for MSSQL adapter."""
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
    interface_version = 1
    driver_modules = ("pyodbc",)
    install_hint = 'pip install "nl2sql-engine[mssql]", and the Microsoft ODBC Driver for SQL Server'

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
        
        query = {key: str(value) for key, value in config.options.items()}
        query["driver"] = config.driver
        if config.trusted_connection:
            query["Trusted_Connection"] = "yes"

        # URL.create escapes the credentials, so a password holding @ : / # %
        # stays a password instead of moving the host or port boundary.
        return URL.create(
            "mssql+pyodbc",
            username=config.user or None,
            password=config.password.get_secret_value() if config.password else None,
            host=config.host,
            port=config.port,
            database=config.database,
            query=query,
        ).render_as_string(hide_password=False)

    def connect(self) -> None:
        """Connects, and enforces ``statement_timeout_ms`` through pyodbc.

        SQL Server has no session setting for a statement timeout; the client
        cancels instead. pyodbc's ``Connection.timeout`` is that query timeout,
        in whole seconds, so it is set on every new pooled connection, rounded
        up so a sub-second limit is not read as "no timeout".
        """
        super().connect()
        if self.statement_timeout_ms:
            event.listen(self.engine, "connect", self._set_query_timeout)

    def _set_query_timeout(self, dbapi_connection, connection_record) -> None:
        dbapi_connection.timeout = math.ceil(self.statement_timeout_ms / 1000)

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
        # A sqlglot dialect name (SQLAlchemy calls it "mssql").
        return "tsql"

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

