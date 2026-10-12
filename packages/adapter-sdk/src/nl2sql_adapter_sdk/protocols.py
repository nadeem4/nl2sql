from typing import Any, Dict, Iterator, Optional, Protocol, Set, runtime_checkable

from .capabilities import DatasourceCapability
from .contracts import AdapterRequest, ResultFrame


@runtime_checkable
class DatasourceAdapterProtocol(Protocol):
    """Contract for adapter implementations."""

    datasource_id: str
    datasource_engine_type: str
    connection_args: Dict[str, Any]
    statement_timeout_ms: Optional[int]
    row_limit: Optional[int]
    max_bytes: Optional[int]

    def capabilities(self) -> Set[DatasourceCapability]:
        """Returns supported capabilities for this adapter."""
        ...

    def connect(self) -> None:
        """Initialize connections / clients based on config."""
        ...

    def fetch_schema_snapshot(self) -> Any:
        """Return a structured schema snapshot if supported."""
        ...

    def execute(self, request: AdapterRequest) -> ResultFrame:
        """Execute a plan-specific request and return a ResultFrame."""
        ...

    def get_dialect(self) -> str:
        """Return the sqlglot dialect name the engine renders SQL in (SQL adapters).

        It must be a name ``sqlglot.Dialect.get_or_raise`` accepts, such as
        ``postgres``, ``tsql``, ``mysql``, ``duckdb`` or ``sqlite``, not the
        SQLAlchemy dialect name (``postgresql``, ``mssql``).
        """
        ...


    def test_connection(self) -> bool:
        """Test if the connection to the datasource can be established."""
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
