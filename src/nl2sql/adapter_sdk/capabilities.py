from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class AdapterCapabilities:
    """
    Defines the capabilities of a Datasource Adapter.
    This allows the Core Orchestrator to decide what tasks can be routed to this adapter.
    """
    
    # --- Execution Capabilities ---
    supports_sql: bool = False
    """If True, the adapter accepts standard SQL strings."""
    
    supports_transactions: bool = False
    """If True, the adapter supports atomic transactions."""
    
    # --- Complexity Capabilities ---
    supports_joins: bool = True
    """If True, the adapter can perform joins natively."""
    
    supports_hueristic_estimation: bool = False
    """If True, the adapter can return cost/row estimates (Pre-flight checks)."""
    
    # --- Advanced Capabilities ---
    supports_vectors: bool = False
    """If True, the adapter has vector search capabilities."""
    
    supported_dialects: List[str] = field(default_factory=list)
    """List of SQL dialects supported (e.g., ['postgres', 'mysql'])."""
