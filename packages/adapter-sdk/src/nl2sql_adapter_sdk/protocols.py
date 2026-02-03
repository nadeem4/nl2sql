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
        """Return the normalized dialect string (SQL adapters)."""
        ...


    def test_connection(self) -> bool:
        """Test if the connection to the datasource can be established."""
        ...

    def get_details(self) -> Dict[str, Any]:
        """Return detailed information about the datasource."""
        ...
