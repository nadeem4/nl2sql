from abc import ABC, abstractmethod
from typing import Any, List, Optional
from .models import SchemaDefinition, ExecutionResult, EstimationResult
from .capabilities import AdapterCapabilities

class DataSourceAdapter(ABC):
    """
    The Protocol/Contract that ALL Datasource Adapters must implement.
    
    This enforces:
    1. Capability Negotiation
    2. Canonical Schema Discovery
    3. Safe Execution
    """
    
    @abstractmethod
    def get_capabilities(self) -> AdapterCapabilities:
        """
        Return the flags indicating what this adapter can and cannot do.
        The Orchestrator uses this to route queries.
        """
        pass
        
    @abstractmethod
    def get_schema(self, table_names: Optional[List[str]] = None) -> SchemaDefinition:
        """
        Retrieve schema metadata.
        
        Args:
            table_names: Optional filter. If None, return all discoverable tables.
        """
        pass
        
    @abstractmethod
    def execute(self, query: Any, **kwargs) -> ExecutionResult:
        """
        Execute the query against the datasource.
        
        Args:
            query: The query object (SQL string, API payload, etc.)
        """
        pass
        
    def estimate(self, query: Any) -> EstimationResult:
        """
        [OPTIONAL] Pre-flight check.
        Perform a dry-run or 'EXPLAIN' to estimate cost and row size.
        
        Default implementation returns a dummy 'Safe' result.
        """
        return EstimationResult(
            estimated_row_count=0,
            will_succeed=True,
            reason="Estimation not implemented"
        )
