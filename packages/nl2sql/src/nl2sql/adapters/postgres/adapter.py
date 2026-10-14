from typing import Literal

from pydantic import Field, SecretStr
from sqlalchemy import URL, Engine, create_engine

from nl2sql_sdk import ConnectionModel
from nl2sql.adapters.sqlalchemy_base import (
    DryRunResult,
    QueryPlan,
    CostEstimate,
    BaseSQLAlchemyAdapter
)


class PostgresConnection(ConnectionModel):
    """A PostgreSQL datasource's ``connection`` block."""

    host: str = Field(title="Host", examples=["db.internal"])
    port: int = Field(5432, title="Port")
    database: str = Field(title="Database")
    user: str = Field(title="User")
    password: SecretStr = Field(title="Password")
    sslmode: Literal["disable", "prefer", "require", "verify-full"] = Field(
        "prefer", title="SSL mode", json_schema_extra={"group": "advanced"})


class PostgresAdapter(BaseSQLAlchemyAdapter):
    title = "PostgreSQL"
    connection_model = PostgresConnection
    driver_modules = ("psycopg2",)
    install_hint = 'pip install "nl2sql-engine[postgres]"'

    def url(self, connection: PostgresConnection) -> URL:
        # Name the driver: the `postgres` extra installs psycopg2, and a bare
        # `postgresql://` means psycopg (v3) from SQLAlchemy 2.1 onwards.
        # URL.create escapes the credentials, so a password holding @ : / # %
        # stays a password instead of moving the host or port boundary.
        return URL.create(
            "postgresql+psycopg2",
            username=connection.user,
            password=connection.password.get_secret_value() or None,
            host=connection.host,
            port=connection.port,
            database=connection.database,
            query={"sslmode": connection.sslmode},
        )

    def build_engine(self) -> Engine:
        """The engine, with ``limits.timeout_ms`` as the server's ``statement_timeout``."""
        connect_args = {}
        if self.limits.timeout_ms:
            connect_args["options"] = f"-c statement_timeout={self.limits.timeout_ms}"
        return create_engine(self.url(self.connection), pool_pre_ping=True, connect_args=connect_args)

    def dry_run(self, sql: str) -> DryRunResult:
        try:
            self.execute_sql(f"EXPLAIN {sql}")
            return DryRunResult(is_valid=True)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, sql: str) -> QueryPlan:
        try:
            res = self.execute_sql(f"EXPLAIN (FORMAT JSON) {sql}")
            return QueryPlan(plan_text=str(res.rows))
        except Exception:            # Fallback
            return QueryPlan(plan_text="Could not retrieve plan")

    def cost_estimate(self, sql: str) -> CostEstimate:
        try:
            res = self.execute_sql(f"EXPLAIN (FORMAT JSON) {sql}")
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
        # A sqlglot dialect name (SQLAlchemy calls it "postgresql").
        return "postgres"


    @property
    def exclude_schemas(self) -> set[str]:
        return {"pg_catalog", "information_schema"}