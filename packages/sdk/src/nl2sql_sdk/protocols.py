from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Iterator, Optional, Protocol, Set, Tuple, Type, runtime_checkable

from .capabilities import DatasourceCapability
from .contracts import AdapterRequest, ResultFrame
from .models import ConnectionCheck, ConnectionModel, Estimate, IndexScope, Limits, Overlap
from .schema import ColumnRef, SchemaSnapshot


@runtime_checkable
class DatasourceAdapter(Protocol):
    """Contract for adapter implementations, version 2.

    Class attributes tell the engine about the adapter before it builds one:

    - ``interface_version``: the version of this contract the adapter was
      written for. It is 2. The engine accepts exactly the version it was
      built for, and anything else is a load error.
    - ``title``: the database's name for people, such as ``"PostgreSQL"``.
    - ``connection_model``: the ``ConnectionModel`` subclass the
      ``connection`` block is validated against. Its JSON Schema is the form
      for connecting a database.
    - ``driver_modules``: the modules the adapter imports to connect, such as
      ``("psycopg2",)``. The engine looks for each with
      ``importlib.util.find_spec`` before it builds the adapter.
    - ``install_hint``: what to install when one of them is missing, such as
      ``pip install "nl2sql-engine[postgres]"``.

    An adapter module must import without its driver: import the driver where
    it is used, not at the top of the module.

    The engine validates the ``connection`` block against
    ``connection_model`` and builds the adapter with
    ``adapter_cls(datasource_id, connection, limits, folder=...)``. Building
    never connects: the adapter connects on first use, and
    ``test_connection`` connects.
    """

    interface_version: ClassVar[int]
    title: ClassVar[str]
    connection_model: ClassVar[Type[ConnectionModel]]
    driver_modules: ClassVar[Tuple[str, ...]]
    install_hint: ClassVar[str]

    datasource_id: str

    def __init__(self, datasource_id: str, connection: ConnectionModel, limits: Limits,
                 *, folder: Optional[Path] = None) -> None:
        """Keeps the settings; never connects. ``folder`` is where a relative file path is read from."""
        ...

    def capabilities(self) -> Set[DatasourceCapability]:
        """Returns supported capabilities for this adapter."""
        ...

    def get_dialect(self) -> str:
        """Return the sqlglot dialect name the engine renders SQL in.

        It must be a name ``sqlglot.Dialect.get_or_raise`` accepts, such as
        ``postgres``, ``tsql``, ``mysql``, ``duckdb`` or ``sqlite``, not the
        SQLAlchemy dialect name (``postgresql``, ``mssql``).
        """
        ...

    def default_schema(self) -> Optional[str]:
        """The schema an unqualified table name resolves to, or None when unknown."""
        ...

    def test_connection(self) -> ConnectionCheck:
        """Connects and reports what it found: ``ok``, or the stage that failed and the fix."""
        ...

    def fetch_schema_snapshot(self, scope: IndexScope,
                              on_table: Optional[Callable[[str], None]] = None) -> SchemaSnapshot:
        """The schema of what ``scope`` wants indexed; ``on_table`` is called with each table's name."""
        ...

    def execute(self, request: AdapterRequest) -> ResultFrame:
        """Execute a plan-specific request and return a ResultFrame."""
        ...

    def close(self) -> None:
        """Releases the adapter's connections."""
        ...


@runtime_checkable
class StreamingAdapterProtocol(Protocol):
    """Optional hook: return a result a batch at a time.

    When the adapter has ``execute_batches``, the engine reads the result
    through it and writes each batch to the result artifact as it arrives, so
    a large result is never held whole in memory. Without the hook the engine
    calls ``execute`` and writes its one ``ResultFrame``, so existing adapters
    keep working.

    Each yielded frame carries the result's ``columns`` and at most
    ``batch_size`` rows; an empty result is one frame with its columns and no
    rows. A failure, before or between batches, is one frame with
    ``success=False`` and an ``error``, and nothing after it: the engine then
    discards what it wrote. Failures are reported the way ``execute`` reports
    them (``retryable`` when the query itself was at fault).
    """

    def execute_batches(self, request: AdapterRequest, batch_size: int) -> Iterator[ResultFrame]:
        """Yield the result of ``request`` in frames of at most ``batch_size`` rows."""
        ...


@runtime_checkable
class SqlRenderingAdapterProtocol(Protocol):
    """Optional hook: render the engine's finished SQL for this database.

    The engine builds each query as a sqlglot expression tree and, when the
    adapter has ``render_sql``, hands it the tree to render. Without the hook
    the engine renders ``expression.sql(dialect=adapter.get_dialect())``, so
    existing adapters keep working. Override it only to rewrite what sqlglot
    cannot express for the database, and keep the result types every adapter
    returns: a date part (``DATE_PART``) is an INTEGER, and a truncated date
    (``DATE_TRUNC``) is an ISO date string ``YYYY-MM-DD``.
    """

    def render_sql(self, expression: Any) -> str:
        """Return the SQL text for ``expression`` (a ``sqlglot.exp.Expression``)."""
        ...


@runtime_checkable
class EstimatingAdapterProtocol(Protocol):
    """Optional hook: the database's estimate of a query, without running it.

    An adapter that has it declares ``DatasourceCapability.ESTIMATES_COST``;
    the engine's cost rule (``policies.cost``) asks for one estimate per
    sub-query before it runs. ``Estimate.rows`` is the rows the query *reads*
    (see ``Estimate``). It runs under the datasource's ``timeout_ms`` and
    never runs the statement. Raise on failure: the rule treats a failed
    estimate as unknown.
    """

    def estimate(self, sql: str, parameters: Dict[str, Any]) -> Estimate:
        """The database's estimate of the rows ``sql`` reads, without executing it. Raises on failure."""
        ...


@runtime_checkable
class ProfilingAdapterProtocol(Protocol):
    """Optional hook: how well one column's values match another's, for the schema profiler's join candidates.

    The SDK's SQLAlchemy base implements it with SQLAlchemy Core, so an
    adapter on it has it. Without it, the profiler proposes joins by name and
    type alone, with no overlap.
    """

    def probe_overlap(self, left: ColumnRef, right: ColumnRef, sample_rows: int) -> Overlap:
        """Of up to ``sample_rows`` distinct non-null values of ``left``, how many exist in ``right``.

        Read-only, within the adapter's limits.
        """
        ...
