import threading

from pydantic import Field
from sqlalchemy import Engine, event

from nl2sql_sdk import ConnectionModel
from nl2sql.adapters.sqlalchemy_base import (
    CostEstimate,
    DryRunResult,
    QueryPlan,
    BaseSQLAlchemyAdapter
)

# Connection.info key of the running statement's interrupt timer.
_TIMER = "nl2sql_statement_timer"


class DuckdbConnection(ConnectionModel):
    """A DuckDB datasource's ``connection`` block."""

    database: str = Field(title="Database file", description="A path, relative to nl2sql.yaml, or ':memory:'",
                          examples=["warehouse.duckdb"])


class DuckdbAdapter(BaseSQLAlchemyAdapter):
    title = "DuckDB"
    connection_model = DuckdbConnection
    driver_modules = ("duckdb", "duckdb_engine")
    install_hint = 'pip install "nl2sql-engine[duckdb]"'

    def url(self, connection: DuckdbConnection) -> str:
        return f"duckdb:///{self.local_path(connection.database)}"

    def build_engine(self) -> Engine:
        """The engine, enforcing ``limits.timeout_ms`` by interrupting.

        DuckDB has no statement timeout setting, so each statement starts a
        timer that calls the connection's ``interrupt()`` when the limit
        passes; the statement then fails with DuckDB's ``InterruptException``,
        reported as a refusal. The timer is cancelled when the statement
        finishes or fails.
        """
        engine = super().build_engine()
        if not self.limits.timeout_ms:
            return engine
        seconds = self.limits.timeout_ms / 1000

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

        event.listen(engine, "before_cursor_execute", start_timer)
        event.listen(engine, "after_cursor_execute", stop_timer)
        event.listen(engine, "handle_error", stop_timer_on_error)
        return engine

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
