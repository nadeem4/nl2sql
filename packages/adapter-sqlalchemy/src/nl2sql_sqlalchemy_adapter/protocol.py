from typing import Protocol, runtime_checkable, Optional, Any
from .models import QueryResult, DryRunResult, QueryPlan, CostEstimate

@runtime_checkable
class SQLAlchemyAdapterProtocol(Protocol):
    """
    Structural definition of a SQLAlchemy Adapter.
    Any class implementing these methods is considered a valid adapter by the Orchestrator.
    """

    @property
    def datasource_id(self) -> str:
        """Unique identifier for this datasource instance."""
        ...

    @property
    def exclude_schemas(self) -> set[str]:
        """Schemas to exclude from indexing."""
        ...


    def connect(self) -> None:
        """Initialize connections / clients based on config."""
        ...

    def fetch_schema_snapshot(self) -> SchemaSnapshot:
        """
        Return the structured schema representation.
        Returns Any here to avoid circular dependencies with adapter packages,
        but implementations typically return a SchemaSnapshot.
        """
        ...

    def execute(self, sql: str) -> QueryResult:
        """Execute query and return normalized results."""
        ...

    def dry_run(self, sql: str) -> DryRunResult:
        """Validate query without fully executing it."""
        ...

    def explain(self, sql: str) -> QueryPlan:
        """Return query plan for observability."""
        ...

    def cost_estimate(self, sql: str) -> CostEstimate:
        """Optional: estimated cost / rows / time."""
        ...

    def get_dialect(self) -> str:
        """Return the normalized dialect string (e.g. 'postgres')."""
        ...
