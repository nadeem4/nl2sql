import threading
from typing import Dict

from pydantic import Field
from sqlalchemy import Connection, Engine, event, text

from nl2sql_sdk import ConnectionModel
from nl2sql_sdk.sqlalchemy import SQLAlchemyAdapter

# Connection.info key of the running statement's interrupt timer.
_TIMER = "nl2sql_statement_timer"


class DuckdbConnection(ConnectionModel):
    """A DuckDB datasource's ``connection`` block."""

    database: str = Field(title="Database file", description="A path, relative to nl2sql.yaml, or ':memory:'",
                          examples=["warehouse.duckdb"])


class DuckdbAdapter(SQLAlchemyAdapter):
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

    def get_dialect(self) -> str:
        return "duckdb"

    def system_schemas(self) -> set[str]:
        # DuckDB qualifies schema names with their catalog; only the attached
        # database's own schemas carry user tables.
        return {"system.main", "system.information_schema", "temp.main"}

    def row_estimates(self, conn: Connection, schema: str) -> Dict[str, int]:
        """``duckdb_tables().estimated_size``. ``schema`` is ``catalog.schema``, as DuckDB lists it."""
        database, _, name = schema.rpartition(".")
        rows = conn.execute(text(
            "SELECT table_name, estimated_size FROM duckdb_tables() "
            "WHERE schema_name = :schema AND (:database = '' OR database_name = :database)"
        ), {"schema": name, "database": database})
        return {table: int(count) for table, count in rows if count is not None}
