import json
import threading
from typing import Any, Callable, Dict, Iterator, List, Optional

from pydantic import Field
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.exc import SQLAlchemyError

from nl2sql_sdk import ConnectionModel, DatasourceCapability, Estimate, ForeignKeyContract, IndexIssue, TableRef
from nl2sql_sdk.sqlalchemy import ReflectedTable, SQLAlchemyAdapter, driver_message

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

    def _in_memory(self) -> bool:
        return self.connection.database == ":memory:"

    def capabilities(self) -> set[DatasourceCapability]:
        """Read-only for a file; DuckDB cannot open an in-memory database read-only, nor has it anything to protect."""
        enforced = {DatasourceCapability.ENFORCES_TIMEOUT, DatasourceCapability.ESTIMATES_COST}
        if not self._in_memory():
            enforced.add(DatasourceCapability.ENFORCES_READ_ONLY)
        return super().capabilities() | enforced

    def build_engine(self) -> Engine:
        """The engine on a file opened read-only, enforcing ``limits.timeout_ms`` by interrupting.

        ``read_only=True`` is how DuckDB opens a file that cannot be written,
        so it is a connect argument rather than a session setting. DuckDB has
        no statement timeout setting, so each statement starts a timer that
        calls the connection's ``interrupt()`` when the limit passes; the
        statement then fails with DuckDB's ``InterruptException``, reported as
        a refusal. The timer is cancelled when the statement finishes or fails.
        """
        engine = super().build_engine(connect_args={} if self._in_memory() else {"read_only": True})
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

    def estimate(self, sql: str, parameters: Dict[str, Any]) -> Estimate:
        """The rows ``sql`` reads, from ``EXPLAIN (FORMAT JSON)``, without running it.

        A scan of a table counts the table's cardinality
        (``duckdb_tables().estimated_size``), not the operator's estimate,
        which is its output after the filter. A table function (``range``,
        ``read_parquet``) counts its own estimate. DuckDB reports no cost.
        """
        try:
            with self.engine.connect() as conn:
                explained = self._run(conn, f"EXPLAIN (FORMAT JSON) {sql}", parameters).all()
                nodes = [node for _, plan in explained for node in _nodes(json.loads(plan))]
                tables = {node["extra_info"]["Table"] for node in nodes if "Table" in node.get("extra_info", {})}
                sizes: Dict[str, int] = {}
                for schema in {name.rpartition(".")[0] for name in tables}:
                    sizes.update({f"{schema}.{table}": rows for table, rows in self.row_estimates(conn, schema).items()})
        except SQLAlchemyError as exc:
            raise RuntimeError(f"{self} could not estimate the query: {driver_message(exc)}") from None
        rows = 0
        for node in nodes:
            info = node.get("extra_info", {})
            if "Table" in info:
                rows += sizes.get(info["Table"], _cardinality(info))
            elif "Function" in info:
                rows += _cardinality(info)
        return Estimate(rows=rows, source="EXPLAIN (FORMAT JSON) and duckdb_tables()")

    def classify(self, exc: BaseException) -> Optional[str]:
        """A file that is not there is the ``database`` stage: read-only never creates one."""
        if "does not exist" in str(getattr(exc, "orig", None) or exc):
            return "database"
        return super().classify(exc)

    def get_dialect(self) -> str:
        return "duckdb"

    def system_schemas(self) -> set[str]:
        # DuckDB qualifies schema names with their catalog; only the attached
        # database's own schemas carry user tables.
        return {"system.main", "system.information_schema", "temp.main"}

    def schema_names(self, conn: Connection) -> List[str]:
        """``catalog.schema`` of each schema in ``duckdb_schemas()``.

        Read from DuckDB's own catalog: duckdb-engine reflects with SQLAlchemy's
        PostgreSQL reflection, which queries ``pg_catalog`` tables DuckDB lacks.
        """
        rows = conn.execute(text(
            "SELECT database_name, schema_name FROM duckdb_schemas() "
            "WHERE schema_name NOT LIKE 'pg\\_%' ESCAPE '\\' ORDER BY database_name, schema_name"))
        return [f"{database}.{name}" for database, name in rows]

    def reflect(self, conn: Connection, schema: str, wants_table: Callable[[str], bool],
                issues: List[IndexIssue]) -> Dict[str, ReflectedTable]:
        """Tables and views from ``duckdb_tables()``, ``duckdb_views()``, ``duckdb_columns()``
        and ``duckdb_constraints()``, with DuckDB's own type names (``DECIMAL(10,2)``, ``VARCHAR[]``)."""
        where = "WHERE schema_name = :schema AND (:database = '' OR database_name = :database)"
        database, _, name = schema.rpartition(".")
        params = {"schema": name, "database": database}
        comments = dict(conn.execute(text(
            f"SELECT table_name, comment FROM duckdb_tables() {where} "
            f"UNION ALL SELECT view_name, comment FROM duckdb_views() {where} AND NOT internal"), params).all())
        wanted = {table for table in comments if wants_table(table)}
        if not wanted:
            return {}

        columns: Dict[str, List[Dict[str, Any]]] = {}
        for table, column, data_type, nullable, comment in conn.execute(text(
                f"SELECT table_name, column_name, data_type, is_nullable, comment FROM duckdb_columns() "
                f"{where} ORDER BY table_name, column_index"), params):
            if table in wanted:
                columns.setdefault(table, []).append(
                    {"name": column, "type": data_type, "nullable": nullable, "comment": comment})

        primary_keys: Dict[str, set] = {}
        foreign_keys: Dict[str, List[ForeignKeyContract]] = {}
        for table, kind, constrained, referred_table, referred in conn.execute(text(
                f"SELECT table_name, constraint_type, constraint_column_names, referenced_table, "
                f"referenced_column_names FROM duckdb_constraints() {where} "
                f"AND constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY') ORDER BY table_name, constraint_index"),
                params):
            if kind == "PRIMARY KEY":
                primary_keys[table] = set(constrained)
            else:
                # DuckDB keeps a foreign key in its own schema.
                foreign_keys.setdefault(table, []).append(ForeignKeyContract(
                    constrained_columns=list(constrained),
                    referred_table=TableRef(schema_name=schema, table_name=referred_table),
                    referred_columns=list(referred)))

        return {
            table: ReflectedTable(columns=columns[table], primary_key=primary_keys.get(table, set()),
                                  foreign_keys=foreign_keys.get(table, []), comment=comments[table])
            for table in sorted(wanted) if table in columns
        }

    def row_estimates(self, conn: Connection, schema: str) -> Dict[str, int]:
        """``duckdb_tables().estimated_size``. ``schema`` is ``catalog.schema``, as DuckDB lists it."""
        database, _, name = schema.rpartition(".")
        rows = conn.execute(text(
            "SELECT table_name, estimated_size FROM duckdb_tables() "
            "WHERE schema_name = :schema AND (:database = '' OR database_name = :database)"
        ), {"schema": name, "database": database})
        return {table: int(count) for table, count in rows if count is not None}


def _nodes(plan: Any) -> Iterator[Dict[str, Any]]:
    """Every operator of an ``EXPLAIN (FORMAT JSON)`` plan: a list of trees of ``name``, ``children``, ``extra_info``."""
    for node in plan if isinstance(plan, list) else [plan]:
        yield node
        yield from _nodes(node.get("children") or [])


def _cardinality(info: Dict[str, Any]) -> int:
    try:
        return int(info.get("Estimated Cardinality", 0))
    except (TypeError, ValueError):
        return 0
