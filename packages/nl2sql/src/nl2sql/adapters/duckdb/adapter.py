import threading
from typing import Any, Dict

from sqlalchemy import event

from nl2sql.adapters.sqlalchemy_base import (
    CostEstimate,
    DryRunResult,
    QueryPlan,
    BaseSQLAlchemyAdapter
)

from pydantic import BaseModel, Field

# Connection.info key of the running statement's interrupt timer.
_TIMER = "nl2sql_statement_timer"


class DuckdbConnectionConfig(BaseModel):
    """Strict configuration schema for DuckDB adapter."""
    type: str
    database: str = Field(..., description="Path to DuckDB database file, or ':memory:'")
    options: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class DuckdbAdapter(BaseSQLAlchemyAdapter):
    interface_version = 1
    driver_modules = ("duckdb", "duckdb_engine")
    install_hint = 'pip install "nl2sql-engine[duckdb]"'

    def construct_uri(self, args: Dict[str, Any]) -> str:
        """Constructs the DuckDB connection URI.

        Args:
            args: The raw connection arguments dictionary.

        Returns:
            str: The fully constructed SQLAlchemy connection URI.

        Raises:
            ValidationError: If the configuration is invalid.
        """
        config = DuckdbConnectionConfig(**args)
        return f"duckdb:///{config.database}"

    def connect(self) -> None:
        """Connects, and enforces ``statement_timeout_ms`` by interrupting.

        DuckDB has no statement timeout setting, so each statement starts a
        timer that calls the connection's ``interrupt()`` when the limit
        passes; the statement then fails with DuckDB's ``InterruptException``,
        reported as a refusal. The timer is cancelled when the statement
        finishes or fails.
        """
        super().connect()
        if not self.statement_timeout_ms:
            return
        seconds = self.statement_timeout_ms / 1000

        def start_timer(conn, cursor, statement, parameters, context, executemany):
            timer = threading.Timer(seconds, conn.connection.dbapi_connection.interrupt)
            timer.daemon = True
            conn.info[_TIMER] = timer
            timer.start()

        def stop_timer(conn, *args):
            timer = conn.info.pop(_TIMER, None)
            if timer is not None:
                timer.cancel()

        def stop_timer_on_error(context):
            if context.connection is not None:
                stop_timer(context.connection)

        event.listen(self.engine, "before_cursor_execute", start_timer)
        event.listen(self.engine, "after_cursor_execute", stop_timer)
        event.listen(self.engine, "handle_error", stop_timer_on_error)

    def dry_run(self, query: str) -> DryRunResult:
        try:
            self.execute_sql(f"EXPLAIN {query}")
            return DryRunResult(is_valid=True, error_message=None)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, query: str) -> QueryPlan:
        try:
            res = self.execute_sql(f"EXPLAIN {query}")
            # EXPLAIN yields (explain_key, explain_value) rows; the plan is the value.
            plan_text = "\n".join(str(row[-1]) for row in res.rows)
            return QueryPlan(plan_text=plan_text)
        except Exception:
            return QueryPlan(plan_text="Could not retrieve plan")

    def cost_estimate(self, query: str) -> CostEstimate:
        # DuckDB's EXPLAIN prints an operator tree with no cost or cardinality
        # figures, so there is nothing real to report here.
        try:
            self.execute_sql(f"EXPLAIN {query}")
            return CostEstimate(estimated_cost=1.0, estimated_rows=10) # Stub
        except Exception:
            return CostEstimate(estimated_cost=-1.0, estimated_rows=0)

    def get_dialect(self) -> str:
        return "duckdb"

    @property
    def exclude_schemas(self) -> set[str]:
        # DuckDB qualifies schema names with their catalog; only the attached
        # database's own schemas carry user tables.
        return {"system.main", "system.information_schema", "temp.main"}
