"""``SQLAlchemyAdapter``: connection, execution and schema reading for any database SQLAlchemy reaches."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Literal, NamedTuple, Optional, Set, Union

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveFloat, PositiveInt
from sqlalchemy import URL, Connection, Engine, case, column, create_engine, event, func, inspect, literal_column, \
    select, table, text
from sqlalchemy.engine.reflection import Inspector, ObjectKind
from sqlalchemy.exc import DataError, ProgrammingError, SQLAlchemyError

from nl2sql_sdk.capabilities import DatasourceCapability
from nl2sql_sdk.contracts import AdapterRequest, ResultError, ResultFrame
from nl2sql_sdk.models import ConnectionCheck, ConnectionModel, IndexScope, Limits, Overlap
from nl2sql_sdk.schema import (
    ColumnContract,
    ColumnRef,
    ColumnMetadata,
    ColumnStatistics,
    ForeignKeyContract,
    IndexIssue,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)

from .profiling import ALL_VALUES_MAX_DISTINCT, SAMPLE_VALUES, ColumnProfile, as_scalar, column_profile

logger = logging.getLogger(__name__)

# Driver messages that mean the database refused the query, whatever its kind
# (MySQL's "SELECT command denied" carries SQLSTATE 42000, a query mistake's class, and
# DuckDB reports a write to a read-only file as invalid input).
_REFUSAL_WORDS = ("permission denied", "permission was denied", "access denied", "command denied",
                  "not authorized", "read-only", "read only", "readonly")
# What a driver says when a statement was stopped for running too long: PostgreSQL's
# "statement timeout" (57014), ODBC's "Query timeout expired" (HYT00), MySQL's
# "maximum statement execution time exceeded", and the interrupt SQLite and DuckDB report.
_TIMEOUT_SQLSTATES = {"57014", "HYT00", "HYT01"}
_TIMEOUT_WORDS = ("timeout", "timed out", "interrupted", "execution time exceeded")
_MAX_MESSAGE = 200

Stage = Literal["config", "driver", "network", "auth", "database", "permission"]

# The standard SQLSTATE classes of a failed connection.
_STAGE_BY_SQLSTATE_CLASS = {"08": "network", "28": "auth", "3D": "database"}

# What to do about a connection that failed at each stage. ``driver``'s fix is the adapter's install_hint.
_FIXES = {
    "network": "Check the host, port and firewall: the database server could not be reached.",
    "auth": "Check the user and password.",
    "database": "Check `database`: it does not exist, or this login cannot open it.",
    "permission": "Connected, but the catalog could not be read: grant read access to the schemas you index.",
}


class Pool(BaseModel):
    """The connection pool of one datasource.

    With no overflow, ``size`` is also the most queries NL2SQL runs against
    the database at once, so it cannot overload a production server.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    size: PositiveInt = Field(5, title="Pool size", description="Connections kept open")
    max_overflow: NonNegativeInt = Field(0, title="Overflow", description="Extra connections allowed under load")
    timeout_s: PositiveFloat = Field(30, title="Wait (s)", description="How long a query waits for a connection")
    recycle_s: PositiveInt = Field(1800, title="Recycle (s)", description="Connections older than this are replaced")


class SQLAlchemyConnection(ConnectionModel):
    """The base of a connection to a database server: adds ``pool``.

    A file database (SQLite, DuckDB) has no server to protect and declares
    its connection on ``ConnectionModel`` instead.
    """

    pool: Pool = Field(default_factory=Pool, title="Connection pool", json_schema_extra={"group": "advanced"})


def _estimated_bytes(rows: List[List[Any]]) -> int:
    """Rough size of ``rows``: the text length of up to 50 of them, scaled to all."""
    if not rows:
        return 0
    sample = rows[:50]
    sample_bytes = sum(len(str(item)) for row in sample for item in row if item is not None)
    return int(sample_bytes / len(sample) * len(rows))


def _sqlstate(exc: BaseException):
    """The five-character SQLSTATE the driver attached, if any."""
    orig = getattr(exc, "orig", None)
    for attr in ("sqlstate", "pgcode"):
        value = getattr(orig, attr, None)
        if isinstance(value, str) and len(value) == 5:
            return value
    return None


def _timed_out(exc: BaseException) -> bool:
    """Whether the server or the driver stopped the statement for running past the timeout."""
    orig = getattr(exc, "orig", None) or exc
    args = getattr(orig, "args", ())
    if _sqlstate(exc) in _TIMEOUT_SQLSTATES or (args and args[0] in _TIMEOUT_SQLSTATES):
        return True
    return any(word in str(orig).lower() for word in _TIMEOUT_WORDS)


def driver_message(exc: BaseException) -> str:
    """The driver's own first line, without SQLAlchemy's SQL echo and links."""
    orig = getattr(exc, "orig", None) or exc
    lines = [line.strip() for line in str(orig).splitlines() if line.strip()]
    first = lines[0] if lines else type(orig).__name__
    return first if len(first) <= _MAX_MESSAGE else first[: _MAX_MESSAGE - 3] + "..."


def _error_type(exc: BaseException) -> str:
    """The driver's exception class name (SQLAlchemy's wrapper's when there is no driver error)."""
    return type(getattr(exc, "orig", None) or exc).__name__


class ReflectedTable(NamedTuple):
    """One table or view as ``reflect`` reads it.

    ``columns`` are dicts in SQLAlchemy's reflected-column shape: ``name``,
    ``type`` (a SQLAlchemy type, or the database's own type name), and
    optionally ``nullable`` and ``comment``.
    """

    columns: List[Dict[str, Any]]
    primary_key: Set[str]
    foreign_keys: List[ForeignKeyContract]
    comment: Optional[str]


def _by_name(reflected: Dict[Any, Any]) -> Dict[str, Any]:
    """``get_multi_*`` results keyed by table name: their keys are ``(schema, name)``."""
    return {key[1]: value for key, value in reflected.items()}


class SQLAlchemyAdapter:
    """The base of an adapter on SQLAlchemy: connection, execution and schema reading.

    A subclass sets the class attributes of ``DatasourceAdapter`` and
    implements ``url`` and ``get_dialect``. Its other overridable methods are
    ``session_setup``, ``classify``, ``system_schemas``, ``schema_names``,
    ``reflect``, ``row_estimates``, ``column_profile`` and ``render_sql``. Building one never connects: the SQLAlchemy engine is
    created on first use, so the driver is imported then.

    It has ``probe_overlap`` (``ProfilingAdapterProtocol``). It has no
    ``estimate``: reading a plan is the database's own, so an adapter that
    can adds ``estimate`` and declares ``ESTIMATES_COST``.
    """

    interface_version = 2

    def __init__(self, datasource_id: str, connection: ConnectionModel, limits: Limits,
                 *, folder: Optional[Path] = None):
        """Keeps the settings. Never connects.

        Args:
            datasource_id: The datasource's id.
            connection: The validated ``connection_model`` instance.
            limits: The datasource's limits. Each adapter enforces
                ``timeout_ms`` the way its driver allows (see ``build_engine``).
            folder: The folder of the nl2sql.yaml the datasource came from, or
                None for the working directory. A relative file path is read from it.
        """
        self.datasource_id = datasource_id
        self.connection = connection
        self.limits = limits
        self.folder = folder
        self._engine: Optional[Engine] = None
        self._engine_lock = threading.Lock()

    # --- What a subclass provides ------------------------------------------

    def url(self, connection: ConnectionModel) -> Union[URL, str]:
        """The SQLAlchemy URL for ``connection``. Build it with ``URL.create``, so credentials are escaped."""
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement url")

    def get_dialect(self) -> str:
        """The sqlglot dialect name of this database."""
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement get_dialect")

    def session_setup(self, dbapi_connection: Any) -> None:
        """Prepares each new pooled connection before its first query: the one place for session settings.

        Make the session read-only and set the statement timeout
        (``limits.timeout_ms``) here, where the database allows, and declare
        ``ENFORCES_READ_ONLY`` and ``ENFORCES_TIMEOUT`` in ``capabilities``
        only for what it really enforces. ``dbapi_connection`` is the
        driver's own connection. The default does nothing.
        """

    def classify(self, exc: BaseException) -> Optional[Stage]:
        """The stage a failed connection test failed at, or None when the error does not say.

        The default reads a missing driver module (``driver``) and the
        standard SQLSTATE: class ``08`` network, ``28`` auth, ``3D`` database,
        and ``42501`` (insufficient privilege) permission. Override it to add
        the driver's own error codes, calling ``super()`` for the rest.
        """
        if isinstance(exc, ImportError):
            return "driver"
        state = _sqlstate(exc)
        if state is None:
            return None
        if state == "42501":
            return "permission"
        return _STAGE_BY_SQLSTATE_CLASS.get(state[:2].upper())

    def system_schemas(self) -> set[str]:
        """The database's own schemas, never indexed whatever the scope says. Matched ignoring case."""
        return set()

    def schema_names(self, conn: Connection) -> List[str]:
        """The database's schemas, as ``fetch_schema_snapshot`` and ``test_connection`` list them.

        The default asks SQLAlchemy's inspector. Override it, with ``reflect``,
        when the dialect's reflection does not work on the database.
        """
        return inspect(conn).get_schema_names()

    def reflect(self, conn: Connection, schema: str, wants_table: Callable[[str], bool],
                issues: List[IndexIssue]) -> Dict[str, ReflectedTable]:
        """Each table and view of ``schema`` that ``wants_table(name)`` accepts, by name.

        The default reflects with SQLAlchemy's inspector, once per schema
        (``get_multi_*``), falling back to one table at a time; a table that
        cannot be read is an ``IndexIssue`` in ``issues`` and is left out
        when its columns cannot be read. Views are read like tables: a
        datasource's ``preferred_tables`` are usually views its data team built.
        """
        inspector = inspect(conn)
        names = set(inspector.get_table_names(schema=schema))
        try:
            names.update(inspector.get_view_names(schema=schema))
        except Exception as exc:
            self._rollback(conn)
            logger.warning(f"Could not list the views of {schema} in {self}: {driver_message(exc)}")
        wanted = sorted(n for n in names if wants_table(n))
        if not wanted:
            return {}

        columns = self._each(conn, inspector, "columns", schema, wanted, issues, required=True)
        pks = self._each(conn, inspector, "pk_constraint", schema, wanted, issues)
        fks = self._each(conn, inspector, "foreign_keys", schema, wanted, issues)
        comments = (self._each(conn, inspector, "table_comment", schema, wanted, issues)
                    if inspector.dialect.supports_comments else {})
        return {
            name: ReflectedTable(
                columns=columns[name],
                primary_key=set((pks.get(name) or {}).get("constrained_columns") or []),
                foreign_keys=[ForeignKeyContract(constrained_columns=fk["constrained_columns"],
                                                 referred_table=TableRef(schema_name=fk.get("referred_schema") or schema,
                                                                         table_name=fk["referred_table"]),
                                                 referred_columns=fk["referred_columns"])
                              for fk in fks.get(name) or []],
                comment=(comments.get(name) or {}).get("text"),
            )
            for name in wanted if name in columns
        }

    def row_estimates(self, conn: Connection, schema: str) -> Optional[Dict[str, int]]:
        """The catalog's row count of each table in ``schema``, or None when the database keeps none.

        Read from the database's statistics, never by counting. A table left
        out has no estimate; under ``profile: stats`` it is counted instead.
        """
        return None

    def column_profile(self, sa_type: Any) -> ColumnProfile:
        """The statistics a column of the reflected ``sa_type`` gets. The default is ``column_profile``."""
        return column_profile(sa_type)

    def render_sql(self, expression: Any) -> str:
        """Renders the engine's finished sqlglot tree as this database's SQL.

        The default is sqlglot's own rendering for ``get_dialect()``. Override
        it to rewrite what sqlglot cannot express for the database, keeping the
        result types in the SDK contract (a date part is an integer, a
        truncated date a ``YYYY-MM-DD`` string).
        """
        return expression.sql(dialect=self.get_dialect())

    # --- The engine ----------------------------------------------------------

    @property
    def engine(self) -> Engine:
        """The SQLAlchemy engine, created on first use. Creating it does not connect either."""
        with self._engine_lock:
            if self._engine is None:
                self._engine = self.build_engine()
            return self._engine

    def build_engine(self, **options: Any) -> Engine:
        """Creates the SQLAlchemy engine from ``url``, with the connection's ``pool``.

        ``session_setup`` runs on every new pooled connection. Override to
        add driver settings, passing them on as ``options`` (``connect_args``,
        for example).
        """
        pool = getattr(self.connection, "pool", None)
        if isinstance(pool, Pool):
            options = {"pool_size": pool.size, "max_overflow": pool.max_overflow,
                       "pool_timeout": pool.timeout_s, "pool_recycle": pool.recycle_s, **options}
        engine = create_engine(self.url(self.connection), pool_pre_ping=True, **options)
        event.listen(engine, "connect", self._on_connect)
        return engine

    def _on_connect(self, dbapi_connection: Any, connection_record: Any) -> None:
        self.session_setup(dbapi_connection)

    def default_schema(self) -> Optional[str]:
        """The schema an unqualified table name resolves to, or ``None`` if unknown.

        Policies read a plan table with no schema as a table in this schema.
        Asked of the database once and remembered.
        """
        if not hasattr(self, "_default_schema"):
            try:
                self._default_schema = inspect(self.engine).default_schema_name
            except Exception as e:
                logger.warning(f"Could not read the default schema of {self}: {e}")
                return None
        return self._default_schema

    def __str__(self):
        return f"{self.datasource_id} ({getattr(self, 'title', type(self).__name__)})"

    def local_path(self, path: str) -> str:
        """A database file path: a relative one is read from ``folder``, when there is one."""
        if not self.folder or path == ":memory:" or Path(path).is_absolute():
            return path
        return (Path(self.folder) / path).as_posix()

    def close(self) -> None:
        """Disposes the connection pool, if one was made. The engine calls it once, from ``engine.close()``."""
        with self._engine_lock:
            engine, self._engine = self._engine, None
        if engine is not None:
            engine.dispose()

    def capabilities(self) -> set[DatasourceCapability]:
        """SQL, with ``AdapterRequest.parameters`` bound by the driver (``conn.execute(text(sql), parameters)``)."""
        return {DatasourceCapability.SUPPORTS_SQL, DatasourceCapability.SUPPORTS_BOUND_PARAMETERS}

    def test_connection(self) -> ConnectionCheck:
        """Connects, runs ``SELECT 1`` and lists the schemas: ``ok`` with the server's version, or the stage that failed.

        The stage comes from ``classify``; a connection that works but cannot
        list the schemas failed at ``permission``. The message is the
        driver's first line, and the fix says what to check at that stage.
        """
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                info = conn.dialect.server_version_info
                try:
                    self.schema_names(conn)
                except Exception as e:
                    return self._failed_check(e, "permission")
        except Exception as e:
            return self._failed_check(e, self.classify(e))
        return ConnectionCheck(ok=True, server_version=".".join(str(part) for part in info) if info else None)

    def _failed_check(self, exc: BaseException, stage: Optional[Stage]) -> ConnectionCheck:
        logger.error(f"Connection test failed for {self} at {stage or 'an unknown stage'}: {exc}")
        fix = getattr(self, "install_hint", None) if stage == "driver" else _FIXES.get(stage or "")
        return ConnectionCheck(ok=False, stage=stage, message=driver_message(exc), fix=fix)

    # --- Execution -------------------------------------------------------------

    @staticmethod
    def _run(conn: Connection, sql: str, parameters: Optional[Dict[str, Any]]):
        """``conn.execute`` with the values bound by name, never written into ``sql``."""
        if parameters:
            return conn.execute(text(sql), dict(parameters))
        return conn.execute(text(sql))

    def execute_sql(self, sql: str, parameters: Optional[Dict[str, Any]] = None) -> ResultFrame:
        """Runs ``sql``, with ``parameters`` bound by name to its ``:name`` placeholders, never interpolated."""
        start = time.perf_counter()
        with self.engine.connect() as conn:
            result = self._run(conn, sql, parameters)
            if result.returns_rows:
                rows = [list(row) for row in result.fetchall()]
                return self._frame(list(result.keys()), rows, len(rows), start)
            return self._frame([], [], result.rowcount, start)

    def execute(self, request: AdapterRequest) -> ResultFrame:
        """Executes an adapter request and returns a ResultFrame."""
        refused = self._refuse(request)
        if refused is not None:
            return refused
        try:
            return self.execute_sql(request.payload["sql"], request.parameters)
        except SQLAlchemyError as exc:
            return self.query_failed(exc)

    def execute_batches(self, request: AdapterRequest, batch_size: int) -> Iterator[ResultFrame]:
        """Yields the result of a request in frames of at most ``batch_size`` rows.

        The rows are fetched with ``fetchmany`` from a result opened with
        ``stream_results``, which is a server-side cursor on drivers that have
        one (psycopg2, the MySQL drivers) and is ignored by the rest, so no more
        than one batch is held as Python rows at a time. Every frame carries
        the columns; an empty result is one frame with no rows. A failure,
        before the first row or between batches, is one failed frame and the
        last one (see ``StreamingAdapterProtocol``).
        """
        refused = self._refuse(request)
        if refused is not None:
            yield refused
            return
        batch_size = max(1, int(batch_size))
        try:
            with self.engine.connect() as conn:
                start = time.perf_counter()
                result = self._run(conn.execution_options(stream_results=True), request.payload["sql"],
                                   request.parameters)
                if not result.returns_rows:
                    yield self._frame([], [], result.rowcount, start)
                    return
                cols = list(result.keys())
                rows = [list(row) for row in result.fetchmany(batch_size)]
                while True:
                    yield self._frame(cols, rows, len(rows), start)
                    if len(rows) < batch_size:
                        return
                    rows = [list(row) for row in result.fetchmany(batch_size)]
                    if not rows:
                        return
        except SQLAlchemyError as exc:
            yield self.query_failed(exc)

    def _frame(self, cols: List[str], rows: List[List[Any]], row_count: int, start: float) -> ResultFrame:
        return ResultFrame(
            success=True,
            columns=cols,
            rows=rows,
            row_count=row_count,
            bytes=_estimated_bytes(rows),
            datasource_id=self.datasource_id,
            execution_stats={"execution_time_ms": (time.perf_counter() - start) * 1000},
        )

    def _refuse(self, request: AdapterRequest) -> Optional[ResultFrame]:
        """The failed result for a request this adapter cannot run, else None."""
        if request.plan_type.lower() != "sql":
            return self._failed("CAPABILITY_VIOLATION", "SQL adapter received non-SQL request.")
        if not request.payload.get("sql"):
            return self._failed("MISSING_SQL", "SQL adapter received an empty SQL payload.")
        return None

    def _failed(self, code: str, message: str) -> ResultFrame:
        return ResultFrame(
            success=False,
            datasource_id=self.datasource_id,
            error=ResultError(error_code=code, safe_message=message, severity="ERROR", retryable=False,
                              stage="adapter", datasource_id=self.datasource_id),
        )

    def query_failed(self, exc: SQLAlchemyError) -> ResultFrame:
        """The failed result for a query the database raised on.

        ``retryable`` is whether the query itself was at fault
        (``is_retryable_error``); the engine then asks the planner for a new
        one. Its message is the driver's first line, bounded: no SQL echo, no
        stack trace. A refusal gets a generic message, since the driver's
        words can name hosts and users; the log keeps them all. A statement
        stopped for running past ``limits.timeout_ms`` is ``QUERY_TIMEOUT``.
        """
        retryable = self.is_retryable_error(exc)
        logger.warning("Query failed on %s (retryable=%s): %s", self, retryable, exc)
        code = "QUERY_REJECTED" if retryable else "QUERY_REFUSED"
        if retryable:
            message = f"The database rejected the query: {driver_message(exc)}"
        elif _timed_out(exc):
            code, message = "QUERY_TIMEOUT", "The database stopped the query: it ran past the datasource's timeout."
        else:
            message = (
                "The database could not run the query: it refused access, timed out "
                "or could not be reached."
            )
        return ResultFrame(
            success=False,
            datasource_id=self.datasource_id,
            error=ResultError(
                error_code=code,
                safe_message=message,
                severity="ERROR",
                retryable=retryable,
                stage="execute",
                datasource_id=self.datasource_id,
            ),
        )

    def is_retryable_error(self, exc: SQLAlchemyError) -> bool:
        """Whether a new query could succeed where this one failed.

        True for a mistake in the query: bad syntax, an unknown table, column
        or function, a type mismatch. False for a refusal: permission, a
        timeout, a lost connection, a read-only database. A message saying
        access was denied, or that the database is read-only, is a refusal
        whatever its SQLSTATE (MySQL files "SELECT command denied" under
        42000). Otherwise read from the
        standard SQLSTATE when the driver gives one (class 42 and 22 are the
        query's fault, except 42501, insufficient privilege), else from
        SQLAlchemy's DBAPI error kind. An adapter whose driver reports query
        mistakes some other way overrides this.
        """
        if any(word in driver_message(exc).lower() for word in _REFUSAL_WORDS):
            return False
        state = _sqlstate(exc)
        if state:
            return state != "42501" and state[:2] in ("42", "22")
        return isinstance(exc, (ProgrammingError, DataError))

    # --- Overlap ---------------------------------------------------------------

    def probe_overlap(self, left: ColumnRef, right: ColumnRef, sample_rows: int) -> Overlap:
        """Of up to ``sample_rows`` distinct non-null values of ``left``, how many exist in ``right``.

        One statement in SQLAlchemy Core: a ``DISTINCT`` sample of ``left``
        with a limit, and an ``EXISTS`` on ``right`` for each value. It runs on
        the adapter's own connections, so under the session's read-only
        setting and ``limits.timeout_ms`` where the adapter enforces them.
        Distinct values, so one popular key does not fill the sample. A
        failure raises ``RuntimeError`` naming the two columns and the
        driver's error class, never its message: a conversion error quotes
        the value it could not convert.
        """
        left_column = table(left.table.table_name, column(left.column_name), schema=left.table.schema_name
                            ).c[left.column_name]
        right_table = table(right.table.table_name, column(right.column_name), schema=right.table.schema_name)
        sample = (select(left_column.label("value")).where(left_column.is_not(None)).distinct()
                  .limit(max(1, int(sample_rows))).subquery("sample"))
        found = select(literal_column("1")).select_from(right_table).where(
            right_table.c[right.column_name] == sample.c.value).exists()
        stmt = select(func.count(), func.count(case((found, 1)))).select_from(sample)
        try:
            with self.engine.connect() as conn:
                sampled, matched = conn.execute(stmt).one()
        except SQLAlchemyError as exc:
            logger.warning("Overlap of %s with %s failed on %s: %s", _column_name(left), _column_name(right), self,
                           _error_type(exc))
            raise RuntimeError(f"Could not compare {_column_name(left)} with {_column_name(right)} on {self}: "
                               f"the database raised {_error_type(exc)}") from None
        return Overlap(sampled=int(sampled), matched=int(matched or 0))

    # --- Reading the schema ----------------------------------------------------

    def fetch_schema_snapshot(self, scope: IndexScope,
                              on_table: Optional[Callable[[str], None]] = None) -> SchemaSnapshot:
        """The schema of the tables and views ``scope`` wants, with their metadata.

        Each schema is read once (``reflect``), after the scope has left out
        the schemas and tables it does not want, so an excluded schema costs
        no query. Row counts are the catalog's estimates
        (``row_estimates``); ``COUNT(*)`` runs only under ``profile: stats``
        for a table with none. What a table or column is indexed without is
        an ``IndexIssue`` on the snapshot. ``on_table`` is called with each
        table's ``schema.table`` as it is profiled.
        """
        system = {name.lower() for name in self.system_schemas()}
        issues: List[IndexIssue] = []
        contracts: Dict[str, TableContract] = {}
        metadata: Dict[str, TableMetadata] = {}

        with self.engine.connect() as conn:
            schemas = sorted(s for s in self.schema_names(conn)
                             if s.lower() not in system and scope.wants_schema(s))
            for schema in schemas:
                reflected = self.reflect(
                    conn, schema, lambda name: scope.wants_table(TableRef(schema_name=schema, table_name=name)),
                    issues)
                if not reflected:
                    continue
                estimates = self._estimates(conn, schema, issues)
                for name, (columns, pk, fks, comment) in reflected.items():
                    ref = TableRef(schema_name=schema, table_name=name)
                    if on_table is not None:
                        on_table(f"{schema}.{name}")
                    columns = [c for c in columns if scope.wants_column(ref, c["name"])]
                    row_count, source = self._row_count(conn, ref, estimates, scope, issues)
                    contracts[ref.full_name] = TableContract(
                        table=ref,
                        columns={c["name"]: ColumnContract(name=c["name"], data_type=str(c["type"]),
                                                           is_nullable=bool(c.get("nullable", True)),
                                                           is_primary_key=c["name"] in pk)
                                 for c in columns},
                        foreign_keys=fks,
                    )
                    metadata[ref.full_name] = TableMetadata(
                        table=ref,
                        columns=self._profile(conn, ref, columns, row_count, scope, issues),
                        row_count=row_count,
                        row_count_source=source,
                        description=comment,
                    )

        dialect = self.get_dialect()
        return SchemaSnapshot(
            contract=SchemaContract(datasource_id=self.datasource_id, engine_type=dialect, tables=contracts),
            metadata=SchemaMetadata(datasource_id=self.datasource_id, engine_type=dialect, description="",
                                    domains=[], tables=metadata),
            issues=issues,
        )

    def _each(self, conn: Connection, inspector: Inspector, what: str, schema: str, names: List[str],
              issues: List[IndexIssue], required: bool = False) -> Dict[str, Any]:
        """``inspector.get_multi_<what>`` for ``names``, falling back to one table at a time.

        A table that cannot be read is an issue; without its columns it is
        left out. When no table's columns can be read, the error is raised.
        """
        try:
            return _by_name(getattr(inspector, f"get_multi_{what}")(
                schema=schema, filter_names=names, kind=ObjectKind.ANY))
        except Exception as exc:
            self._rollback(conn)
            logger.warning(f"Reflecting the {what} of {schema} at once failed in {self}, "
                           f"reading each table: {driver_message(exc)}")
        found, first_error = {}, None
        for name in names:
            try:
                found[name] = getattr(inspector, f"get_{what}")(name, schema=schema)
            except Exception as exc:
                self._rollback(conn)
                first_error = first_error or exc
                issues.append(IndexIssue(table=f"{schema}.{name}", stage="reflect",
                                         message=f"{what.replace('_', ' ')} not read: {driver_message(exc)}"))
        if required and not found and first_error is not None:
            raise first_error
        return found

    def _estimates(self, conn: Connection, schema: str, issues: List[IndexIssue]) -> Dict[str, int]:
        try:
            return self.row_estimates(conn, schema) or {}
        except Exception as exc:
            self._rollback(conn)
            issues.append(IndexIssue(table=f"{schema}.*", stage="estimate",
                                     message=f"no catalog row estimates: {driver_message(exc)}"))
            return {}

    def _row_count(self, conn: Connection, ref: TableRef, estimates: Dict[str, int], scope: IndexScope,
                   issues: List[IndexIssue]):
        """``(row count, where it came from)``: the catalog's estimate, else ``COUNT(*)`` under ``stats``."""
        if ref.table_name in estimates:
            return int(estimates[ref.table_name]), "catalog"
        if scope.profile != "stats":
            return None, None
        try:
            stmt = select(func.count()).select_from(table(ref.table_name, schema=ref.schema_name))
            return conn.execute(stmt).scalar(), "exact"
        except Exception as exc:
            self._rollback(conn)
            issues.append(IndexIssue(table=_name(ref), stage="estimate",
                                     message=f"rows not counted: {driver_message(exc)}"))
            return None, None

    def _profile(self, conn: Connection, ref: TableRef, columns: List[Dict[str, Any]], row_count: Optional[int],
                 scope: IndexScope, issues: List[IndexIssue]) -> Dict[str, ColumnMetadata]:
        """Each column's description, and its statistics under ``profile: stats``."""
        profiled = scope.profile == "stats"
        if profiled and row_count is not None and row_count > scope.max_profile_rows:
            issues.append(IndexIssue(
                table=_name(ref), stage="profile",
                message=f"about {row_count:,} rows, over max_profile_rows ({scope.max_profile_rows:,}): "
                        f"profiled from the catalog only"))
            profiled = False
        return {
            c["name"]: ColumnMetadata(
                description=c.get("comment"),
                statistics=(self._statistics(conn, ref, c["name"], self.column_profile(c["type"]),
                                             scope.sample_values, issues) if profiled else None),
                synonyms=[],
                pii=False,
            )
            for c in columns
        }

    def _statistics(self, conn: Connection, ref: TableRef, name: str, profile: ColumnProfile,
                    sample_values: bool, issues: List[IndexIssue]) -> Optional[ColumnStatistics]:
        """One column's statistics, in one query, and its values in a second when asked for.

        A failed query becomes an issue and is rolled back, so it cannot
        poison the queries after it (on PostgreSQL it would abort them all).
        """
        t = table(ref.table_name, column(name), schema=ref.schema_name)
        c = t.c[name]
        stmt = select(
            func.count(),
            func.count(case((c.is_(None), 1))),
            func.min(c) if profile.min_max else literal_column("NULL"),
            func.max(c) if profile.min_max else literal_column("NULL"),
            func.count(func.distinct(c)) if profile.distinct else literal_column("0"),
        ).select_from(t)
        try:
            total, nulls, low, high, distinct = conn.execute(stmt).one()
        except Exception as exc:
            self._rollback(conn)
            issues.append(IndexIssue(table=_name(ref), column=name, stage="profile", message=driver_message(exc),
                                     error_type=_error_type(exc)))
            return None

        values: List[Any] = []
        if profile.values and sample_values and distinct:
            limit = None if distinct <= ALL_VALUES_MAX_DISTINCT else SAMPLE_VALUES
            stmt = select(c).select_from(t).where(c.is_not(None)).group_by(c).order_by(func.count().desc(), c)
            try:
                values = [row[0] for row in conn.execute(stmt if limit is None else stmt.limit(limit))]
            except Exception as exc:
                self._rollback(conn)
                issues.append(IndexIssue(table=_name(ref), column=name, stage="values",
                                         message=driver_message(exc), error_type=_error_type(exc)))
        return ColumnStatistics(
            null_percentage=nulls / total if total else 0.0,
            distinct_count=int(distinct or 0),
            min_value=as_scalar(low),
            max_value=as_scalar(high),
            sample_values=values,
        )

    def _rollback(self, conn: Connection) -> None:
        """Ends the transaction a failed statement left behind.

        Reading the schema only reads, so rolling back loses nothing, and it
        works on every dialect, where a savepoint per column would not
        (pysqlite and DuckDB) and would cost a round trip per column where it does.
        """
        try:
            conn.rollback()
        except Exception as exc:
            logger.warning(f"Failed to roll back after a profiling error for {self}: {driver_message(exc)}")


def _name(ref: TableRef) -> str:
    return f"{ref.schema_name}.{ref.table_name}"


def _column_name(ref: ColumnRef) -> str:
    return f"{_name(ref.table)}.{ref.column_name}"
