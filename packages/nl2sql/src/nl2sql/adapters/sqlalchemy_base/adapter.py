import datetime
import decimal
import time
from typing import Dict, Any, Iterator, List, Optional
from sqlalchemy import create_engine, inspect, text, Engine, select, func, table, column, case, literal_column, Connection
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.sql import sqltypes
from typing import Tuple
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql_adapter_sdk.contracts import (
    AdapterRequest,
    ResultError,
    ResultFrame,
)
from .models import DryRunResult, QueryPlan, CostEstimate
from nl2sql_adapter_sdk.schema import (
    SchemaContract,
    SchemaMetadata,
    TableContract,
    ColumnContract,
    ForeignKeyContract,
    ColumnMetadata,
    TableMetadata,
    SchemaSnapshot,
    ColumnStatistics,
    TableRef,
)
from sqlalchemy.exc import DataError, ProgrammingError, SQLAlchemyError
import logging
logger = logging.getLogger(__name__)

# Driver messages that mean the database refused the query, whatever its kind.
_REFUSAL_WORDS = ("permission denied", "permission was denied", "access denied", "not authorized")
_MAX_MESSAGE = 200


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


def _driver_message(exc: BaseException) -> str:
    """The driver's own first line, without SQLAlchemy's SQL echo and links."""
    orig = getattr(exc, "orig", None) or exc
    lines = [line.strip() for line in str(orig).splitlines() if line.strip()]
    first = lines[0] if lines else type(orig).__name__
    return first if len(first) <= _MAX_MESSAGE else first[: _MAX_MESSAGE - 3] + "..."


# A text column with at most this many distinct values is indexed with all of
# them, so a filter on any one ('Heavy Metal' among 25 genres) matches a real
# value rather than a guess; a larger column keeps its five most frequent.
ALL_VALUES_MAX_DISTINCT = 50
SAMPLE_VALUES = 5

# Types MIN/MAX cannot order: Postgres raises on MIN(boolean) and MIN(uuid),
# SQL Server on MIN(bit). Dialect types subclass these (postgresql UUID,
# mssql BIT and UNIQUEIDENTIFIER), so isinstance covers them.
_UNORDERED_TYPES = (sqltypes.Boolean, sqltypes.Uuid)
# Types that get no statistics at all, by class and, for types a driver
# reflects without a SQLAlchemy class, by name.
_UNPROFILED_TYPES = (sqltypes.JSON, sqltypes.LargeBinary, sqltypes.ARRAY)
_UNPROFILED_NAMES = ("json", "blob", "binary", "bytea", "xml", "array")


def min_max_skip_reason(sa_type: Any) -> Optional[str]:
    """Why a column of ``sa_type`` gets no MIN/MAX, or None if it does."""
    if _unprofiled(sa_type):
        return f"{_type_name(sa_type)} is not profiled"
    if isinstance(sa_type, _UNORDERED_TYPES):
        return f"{_type_name(sa_type)} has no ordering"
    return None


def as_scalar(value: Any) -> Any:
    """``value`` as a statistics scalar (str, int, float, bool or None).

    Drivers return dates, times, decimals and UUIDs for MIN/MAX, which the
    statistics model does not accept: dates and times become ISO strings,
    decimals floats, anything else its text.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    return str(value)


def _type_name(sa_type: Any) -> str:
    try:
        return str(sa_type)
    except Exception:
        return type(sa_type).__name__


def _unprofiled(sa_type: Any) -> bool:
    """A type that gets no statistics at all: JSON, binary, XML, arrays."""
    if isinstance(sa_type, _UNPROFILED_TYPES):
        return True
    return any(name in _type_name(sa_type).lower() for name in _UNPROFILED_NAMES)

class BaseSQLAlchemyAdapter:
    """
    Base class for all SQLAlchemy-based adapters.
    Implements common logic for connection, execution, and schema fetching.
    """
    def __init__(self, datasource_id: str = None, datasource_engine_type: str = None, connection_args: Dict[str, Any] = None, **kwargs):
        """Initializes the SQLAlchemy adapter.

        Args:
            datasource_id (str, optional): The unique identifier for the datasource.
            datasource_engine_type (str, optional): The engine type (e.g., 'postgres').
            connection_args (Dict[str, Any], optional): The resolved connection arguments.
            **kwargs: Additional configuration parameters like 'row_limit' and 'max_bytes'.
        """
        self._datasource_id = datasource_id
        self.datasource_engine_type = datasource_engine_type
        self._row_limit = kwargs.get("row_limit")
        self._max_bytes = kwargs.get("max_bytes")
        
        # SQLAlchemy has no generic statement timeout, so each adapter enforces
        # this the way its driver allows (see its connect()).
        self.statement_timeout_ms = kwargs.get("statement_timeout_ms")
        self.execution_options = {}

        self.connection_args = connection_args
        self.connection_string = self.construct_uri(connection_args)

        self.engine: Engine = None
        if self.connection_string:
            self.connect()

    @property
    def datasource_id(self) -> str:
        return self._datasource_id

    @property
    def row_limit(self) -> int:
        return self._row_limit

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def __str__(self):
        return f"{self.datasource_id} ({self.datasource_engine_type})"

    def construct_uri(self, args: Dict[str, Any]) -> str:
        """
        Constructs a SQLAlchemy URL from a connection dictionary.
        Must be implemented by subclasses.
        """
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement construct_uri")

    def connect(self) -> None:
        """Establishes a connection to the database.

        Raises:
            ValueError: If the connection string is missing.
            Exception: If connection fails.
        """
        conn_str = self.connection_string
        if not conn_str:
             raise ValueError(f"Connection string is required for {self}")
        try:
            self.engine = create_engine(
                conn_str, 
                pool_pre_ping=True,
                execution_options=self.execution_options
            )
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise


    def capabilities(self) -> set[DatasourceCapability]:
        """Default capability set for SQL adapters.

        Every SQLAlchemy adapter binds ``AdapterRequest.parameters`` through
        the driver (``conn.execute(text(sql), parameters)``), so each declares
        ``SUPPORTS_BOUND_PARAMETERS``.
        """
        return {DatasourceCapability.SUPPORTS_SQL, DatasourceCapability.SUPPORTS_BOUND_PARAMETERS}

    @staticmethod
    def _run(conn: Connection, sql: str, parameters: Optional[Dict[str, Any]]):
        """``conn.execute`` with the values bound by name, never written into ``sql``.

        Without parameters the call is exactly ``conn.execute(text(sql))``, so
        generated SQL runs as it always has.
        """
        if parameters:
            return conn.execute(text(sql), dict(parameters))
        return conn.execute(text(sql))

    def execute_sql(self, sql: str, parameters: Optional[Dict[str, Any]] = None) -> ResultFrame:
        """Executes a SQL query against the datasource.

        Args:
            sql (str): The SQL query string to execute.
            parameters: Values bound by name to the ``:name`` placeholders in
                ``sql``; never interpolated into it.

        Returns:
            ResultFrame: The results of the query execution.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        if not self.engine:
            raise RuntimeError(f"Not connected to {self}")

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
        if not self.engine:
            raise RuntimeError(f"Not connected to {self}")
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
            return ResultFrame(
                success=False,
                datasource_id=self.datasource_id,
                error=ResultError(
                    error_code="CAPABILITY_VIOLATION",
                    safe_message="SQL adapter received non-SQL request.",
                    severity="ERROR",
                    retryable=False,
                    stage="adapter",
                    datasource_id=self.datasource_id,
                ),
            )

        sql = request.payload.get("sql")
        if not sql:
            return ResultFrame(
                success=False,
                datasource_id=self.datasource_id,
                error=ResultError(
                    error_code="MISSING_SQL",
                    safe_message="SQL adapter received an empty SQL payload.",
                    severity="ERROR",
                    retryable=False,
                    stage="adapter",
                    datasource_id=self.datasource_id,
                ),
            )
        return None

    def query_failed(self, exc: SQLAlchemyError) -> ResultFrame:
        """The failed result for a query the database raised on.

        ``retryable`` is whether the query itself was at fault
        (``is_retryable_error``); the engine then asks the planner for a new
        one. Its message is the driver's first line, bounded: no SQL echo, no
        stack trace. A refusal gets a generic message, since the driver's
        words can name hosts and users; the log keeps them all.
        """
        retryable = self.is_retryable_error(exc)
        logger.warning("Query failed on %s (retryable=%s): %s", self, retryable, exc)
        if retryable:
            message = f"The database rejected the query: {_driver_message(exc)}"
        else:
            message = (
                "The database could not run the query: it refused access, timed out "
                "or could not be reached."
            )
        return ResultFrame(
            success=False,
            datasource_id=self.datasource_id,
            error=ResultError(
                error_code="QUERY_REJECTED" if retryable else "QUERY_REFUSED",
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
        timeout, a lost connection, a read-only database. Read from the
        standard SQLSTATE when the driver gives one (class 42 and 22 are the
        query's fault, except 42501, insufficient privilege), otherwise from
        SQLAlchemy's DBAPI error kind. An adapter whose driver reports query
        mistakes some other way overrides this.
        """
        state = _sqlstate(exc)
        if state:
            return state != "42501" and state[:2] in ("42", "22")
        if any(word in _driver_message(exc).lower() for word in _REFUSAL_WORDS):
            return False
        return isinstance(exc, (ProgrammingError, DataError))


    @property
    def exclude_schemas(self) -> set[str]:
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement exclude_schemas")


    def _relation_names(self, inspector: Inspector, schema: str) -> List[str]:
        """The schema's tables and views, sorted.

        Views are snapshotted like tables: a datasource's ``preferred_tables``
        are usually views its data team built. A driver that cannot list views
        leaves them out rather than failing the snapshot.
        """
        names = set(inspector.get_table_names(schema=schema))
        try:
            names.update(inspector.get_view_names(schema=schema))
        except Exception as e:
            logger.warning(f"Failed to list views in schema {schema} for {self}: {e}")
        return sorted(names)

    def fetch_schema_contact(self) -> SchemaContract:
        """Fetches the schema contract for the connected datasource.

        Returns:
            SchemaContract: The contract containing tables, columns, and relationships.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        if not self.engine:
            raise RuntimeError(f"Not connected to {self}. Please verify the connection details.")

        exclude_schemas = {schema.lower() for schema in self.exclude_schemas}
        inspector = inspect(self.engine)
        tables_contract = {}

        try:
            schemas = [schema for schema in inspector.get_schema_names() if schema.lower() not in exclude_schemas]
        except Exception as e:
            logger.error(f"Failed to fetch table names for {self}: {e}")
            raise

        with self.engine.connect() as conn:
            for schema in sorted(schemas):
                for table_name in self._relation_names(inspector, schema):
                    table_ref = TableRef(schema_name=schema, table_name=table_name)
                    columns_contract = self._get_column_contract(inspector, conn, table_ref)
                    fks = self._get_fk_cols(inspector, table_ref)

                    table_contract = TableContract(
                        table=table_ref,
                        columns=columns_contract,
                        foreign_keys=fks
                    )
                    tables_contract[table_ref.full_name] = table_contract

        return SchemaContract(
            datasource_id=self.datasource_id,
            engine_type=self.datasource_engine_type,
            tables=tables_contract
        )

    def fetch_schema_metadata(self) -> SchemaMetadata:
        """Fetches the schema metadata for the connected datasource.

        Returns:
            SchemaMetadata: The metadata containing tables, columns, and relationships.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        if not self.engine:
            raise RuntimeError(f"Not connected to {self}. Please verify the connection details.")

        exclude_schemas = {schema.lower() for schema in self.exclude_schemas}
        inspector = inspect(self.engine)
        tables_metadata = {}

        try:
            schemas = [schema for schema in inspector.get_schema_names() if schema.lower() not in exclude_schemas]
        except Exception as e:
            logger.error(f"Failed to fetch table names for {self}: {e}")
            raise

        with self.engine.connect() as conn:
            for schema in sorted(schemas):
                for table_name in self._relation_names(inspector, schema):
                    table_ref = TableRef(schema_name=schema, table_name=table_name)
                    try:
                        row_count = self._get_row_count(conn, table_ref)
                    except Exception as e:
                        self._rollback(conn)
                        logger.warning(f"Failed to fetch row count for {self}: {table_ref.full_name}: {_driver_message(e)}")
                        row_count = 0
                    try:
                        columns_metadata = self._get_columns_metadata(inspector, conn, table_ref, row_count)
                    except Exception as e:
                        self._rollback(conn)
                        logger.warning(f"Failed to profile {table_ref.full_name} for {self}: {_driver_message(e)}")
                        columns_metadata = {}
                    try:
                        table_comment = inspector.get_table_comment(
                            table_ref.table_name,
                            schema=table_ref.schema_name
                        ).get("text")
                    except Exception:
                        table_comment = None

                    table_metadata = TableMetadata(
                        table=table_ref,
                        columns=columns_metadata,
                        row_count=row_count,
                        description=table_comment
                    )
                    tables_metadata[table_ref.full_name] = table_metadata

        return SchemaMetadata(
            datasource_id=self.datasource_id,
            engine_type=self.datasource_engine_type,
            description="",
            domains=[],
            tables=tables_metadata
        )

    def fetch_schema_snapshot(self) -> SchemaSnapshot:
        """Fetches the schema metadata for the connected datasource.

        Returns:
            SchemaMetadata: The metadata containing tables, columns, and relationships.

        Raises:
            RuntimeError: If the adapter is not connected.
        """

        schema_contract = self.fetch_schema_contact()
        schema_metadata = self.fetch_schema_metadata()

        return SchemaSnapshot(contract=schema_contract, metadata=schema_metadata)


    def _get_columns_metadata(self, inspector: Inspector, conn: Connection, table_ref: TableRef, row_count: int) -> Dict[str, ColumnMetadata]:
        """Each column's description and statistics, profiled one at a time.

        A column whose statistics query fails keeps no statistics, and the
        read-only transaction is rolled back so the failed statement cannot
        poison the queries after it (on Postgres it would abort them all).
        """
        columns_metadata = {}
        skipped, failed = [], []

        for col_info in inspector.get_columns(table_ref.table_name, schema=table_ref.schema_name):
            name, sa_type = col_info["name"], col_info["type"]
            reason = min_max_skip_reason(sa_type)
            if reason:
                skipped.append(f"{name} ({reason})")
            try:
                statistics = self._get_column_stats(conn, table_ref, name, row_count, sa_type)
            except Exception as e:
                self._rollback(conn)
                failed.append(f"{name} ({_driver_message(e)})")
                statistics = None
            columns_metadata[name] = ColumnMetadata(
                description=col_info.get("comment"),
                statistics=statistics,
                synonyms=[],
                pii=False
            )

        if failed:
            logger.warning(
                f"Profiled {table_ref.full_name} for {self} without statistics for "
                f"{len(failed)} column(s): {'; '.join(failed)}"
                + (f". No MIN/MAX for: {'; '.join(skipped)}" if skipped else "")
            )
        elif skipped:
            logger.debug(f"No MIN/MAX for {table_ref.full_name} in {self}: {'; '.join(skipped)}")
        return columns_metadata

    def _rollback(self, conn: Connection) -> None:
        """Ends a transaction a failed statement left behind.

        Profiling only reads, so rolling back loses nothing, and it works on
        every dialect, where a savepoint per column would not (pysqlite and
        DuckDB) and would cost a round trip per column where it does.
        """
        try:
            conn.rollback()
        except Exception as e:
            logger.warning(f"Failed to roll back after a profiling error for {self}: {_driver_message(e)}")

    def _get_column_contract(self, inspector: Inspector, conn: Connection, table_ref: TableRef) -> Dict[str, ColumnContract]:
        columns_contract = {}
        try:
            pk = inspector.get_pk_constraint(table_ref.table_name, schema=table_ref.schema_name)
            pk_cols = set(pk.get("constrained_columns") or [])
        except Exception as e:
            # A view has no primary key, and some drivers (duckdb-engine) fail
            # to look one up rather than return none.
            logger.warning(f"Failed to fetch the primary key of {table_ref.full_name} for {self}: {e}")
            pk_cols = set()
        for col_info in inspector.get_columns(table_ref.table_name, schema=table_ref.schema_name):
            columns_contract[col_info["name"]] = ColumnContract(
                name=col_info["name"],
                data_type=str(col_info["type"]),
                is_nullable=bool(col_info["nullable"]),
                is_primary_key=col_info["name"] in pk_cols
            )
        return columns_contract

    def _get_fk_cols(self, inspector: Inspector, table_ref: TableRef) -> List[ForeignKeyContract]:
        fks = []
        try:
            for fk_info in inspector.get_foreign_keys(table_ref.table_name, schema=table_ref.schema_name):
                fks.append(ForeignKeyContract(
                    constrained_columns=fk_info["constrained_columns"],
                    referred_table=TableRef(schema_name=fk_info.get("referred_schema"), table_name=fk_info["referred_table"]),
                    referred_columns=fk_info["referred_columns"],
                ))
        except Exception as e:
            logger.warning(f"Failed to fetch foreign keys for {self}: {e}") 
        return fks

    def _get_row_count(self, conn: Connection, table_ref: TableRef) -> int:
        """Fetches the total row count for a specific table.

        Args:
            table_ref (TableRef): The table reference.

        Returns:
            int: The total number of rows.

        Raises:
            Exception: Whatever the driver raised; the caller rolls back.
        """
        stmt = select(func.count()).select_from(table(table_ref.table_name, schema=table_ref.schema_name))
        return conn.execute(stmt).scalar()

    def _get_column_stats(self, conn: Connection, table_ref: TableRef, column_name: str, row_count: int, column_type: Any) -> ColumnStatistics:
        """Fetches statistics for a specific column.

        Args:
            table_ref (TableRef): The table reference.
            column_name (str): The column name.
            row_count (int): Total rows in the table.
            column_type: The reflected SQLAlchemy type.

        Returns:
            ColumnStatistics: Statistical data about the column. Types with no
            ordering (``min_max_skip_reason``) get no MIN/MAX; JSON, binary,
            XML and array columns get no statistics at all.
        """
        if _unprofiled(column_type):
             return ColumnStatistics(
                null_percentage=0.0,
                distinct_count=0,
                min_value=None,
                max_value=None,
                sample_values=[]
            )

        t = table(table_ref.table_name, column(column_name), schema=table_ref.schema_name)
        c = t.c[column_name]
        ordered = min_max_skip_reason(column_type) is None

        stmt = (
            select(
                func.count(case((c == None, 1))),
                func.min(c) if ordered else literal_column("NULL"),
                func.max(c) if ordered else literal_column("NULL"),
                func.count(func.distinct(c))
            )
            .select_from(t)
        )

        result = conn.execute(stmt).fetchone()
        null_count, min_val, max_val, distinct_count = result

        # A generic Uuid reads as CHAR(32), but its values are not text.
        is_text = not isinstance(column_type, _UNORDERED_TYPES) and any(
            t in _type_name(column_type).lower() for t in ['char', 'text', 'string', 'clob'])
        limit = None if distinct_count <= ALL_VALUES_MAX_DISTINCT else SAMPLE_VALUES
        return ColumnStatistics(
            null_percentage=(null_count / row_count) if row_count > 0 else 0,
            distinct_count=distinct_count,
            min_value=as_scalar(min_val),
            max_value=as_scalar(max_val),
            sample_values=self._get_sample_values(conn, table_ref, column_name, limit) if is_text else []
        )

    def _get_sample_values(self, conn: Connection, table_ref: TableRef, column_name: str,
                           limit: Optional[int] = SAMPLE_VALUES) -> List[Any]:
        """A column's values, most frequent first and ties by value.

        Args:
            table_name (str): The table name.
            column_name (str): The column name.
            limit (int, optional): Max values to return; None returns every
                distinct value. Defaults to ``SAMPLE_VALUES``.

        Returns:
            List[Any]: A list of sample values.
        """
        t = table(table_ref.table_name, column(column_name), schema=table_ref.schema_name)
        c = t.c[column_name]

        stmt = (
            select(c)
            .select_from(t)
            .where(c != None)
            .group_by(c)
            .order_by(func.count().desc(), c)
        )
        if limit is not None:
            stmt = stmt.limit(limit)

        return [row[0] for row in conn.execute(stmt).fetchall()]

    
    def dry_run(self, sql: str) -> DryRunResult:
        """
        Generic dry run using transaction rollback.
        Works for SQLite, MySQL, Postgres (if not overridden), etc.
        """
        try:
            with self.engine.connect() as conn:
                trans = conn.begin()
                conn.execute(text(sql))
                trans.rollback()
            return DryRunResult(is_valid=True)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, sql: str) -> QueryPlan:
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement explain")
    
    def get_dialect(self) -> str:
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement get_dialect")

    def render_sql(self, expression: Any) -> str:
        """Renders the engine's finished sqlglot tree as this database's SQL.

        The default is sqlglot's own rendering for ``get_dialect()``. Override
        it to rewrite what sqlglot cannot express for the database, keeping the
        result types in the SDK contract (a date part is an integer, a
        truncated date a ``YYYY-MM-DD`` string).
        """
        return expression.sql(dialect=self.get_dialect())

    def cost_estimate(self, sql: str) -> CostEstimate:
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement cost_estimate")
    

    def test_connection(self) -> bool:
        """
        Tests the database connection by executing a simple query.
        """
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Connection test failed for {self}: {e}")
            return False
        
