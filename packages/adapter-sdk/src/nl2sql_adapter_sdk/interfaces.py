from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from .models import (
    SchemaMetadata,
    QueryResult,
    DryRunResult,
    QueryPlan,
    CostEstimate,
    CapabilitySet,
    CapabilitySet,
)

class DatasourceAdapter(ABC):
    """Canonical interface every adapter must implement."""


    @property
    @abstractmethod
    def datasource_id(self) -> str:
        """Unique identifier for this datasource instance."""
        pass
    
    @property
    @abstractmethod
    def max_bytes(self) -> Optional[int]:
        """Safety limit for maximum bytes to return."""
        pass

    @property
    @abstractmethod
    def row_limit(self) -> Optional[int]:
        """Safety limit for maximum rows to return."""
        pass

    @abstractmethod
    def connect(self) -> None:
        """Initialize connections / clients based on config."""
        pass

    @abstractmethod
    def capabilities(self) -> CapabilitySet:
        """Return what this engine supports (CTEs, window functions, etc.)."""
        pass

    @abstractmethod
    def fetch_schema(self) -> SchemaMetadata:
        """Return canonical structured schema representation."""
        pass

    @abstractmethod
    def dry_run(self, sql: str) -> DryRunResult:
        """Validate query without fully executing it if engine supports it."""
        pass

    @abstractmethod
    def explain(self, sql: str) -> QueryPlan:
        """Return query plan, useful for optimization / observability."""
        pass

    @abstractmethod
    def cost_estimate(self, sql: str) -> CostEstimate:
        """Optional: estimated cost / rows / time."""
        pass

    @abstractmethod
    def execute(self, sql: str) -> QueryResult:
        """Execute query and return normalized results."""
        pass

    @abstractmethod
    def get_dialect(self) -> str:
        """Return the normalized dialect string (e.g. 'postgres', 'tsql')."""
        pass
