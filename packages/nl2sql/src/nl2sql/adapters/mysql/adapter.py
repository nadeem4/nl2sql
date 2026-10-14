from pydantic import Field, SecretStr
from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.dialects import mysql

from nl2sql_sdk import ConnectionModel
from nl2sql.adapters.sqlalchemy_base import (
    CostEstimate,
    DryRunResult,
    QueryPlan,
    BaseSQLAlchemyAdapter
)


class MysqlConnection(ConnectionModel):
    """A MySQL datasource's ``connection`` block."""

    host: str = Field(title="Host", examples=["db.internal"])
    port: int = Field(3306, title="Port")
    database: str = Field(title="Database")
    user: str = Field(title="User")
    password: SecretStr = Field(title="Password")


class MysqlAdapter(BaseSQLAlchemyAdapter):
    title = "MySQL"
    connection_model = MysqlConnection
    driver_modules = ("pymysql",)
    install_hint = 'pip install "nl2sql-engine[mysql]"'

    def url(self, connection: MysqlConnection) -> URL:
        # URL.create escapes the credentials, so a password holding @ : / # %
        # stays a password instead of moving the host or port boundary.
        return URL.create(
            "mysql+pymysql",
            username=connection.user,
            password=connection.password.get_secret_value() or None,
            host=connection.host,
            port=connection.port,
            database=connection.database,
        )

    def build_engine(self) -> Engine:
        """The engine, with ``limits.timeout_ms`` as the session's ``MAX_EXECUTION_TIME`` (server-side)."""
        connect_args = {}
        if self.limits.timeout_ms:
            connect_args["init_command"] = f"SET MAX_EXECUTION_TIME={self.limits.timeout_ms}"
        return create_engine(self.url(self.connection), pool_pre_ping=True, connect_args=connect_args)

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
