from typing import Any, Dict, Optional, Protocol, Set, runtime_checkable

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
