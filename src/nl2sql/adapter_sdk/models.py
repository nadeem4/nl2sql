from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class ColumnDefinition:
    """Canonical representation of a table column."""
    name: str
    data_type: str
    is_primary_key: bool = False
    description: Optional[str] = None

@dataclass
class TableDefinition:
    """Canonical representation of a table or relational view."""
    name: str
    columns: List[ColumnDefinition]
    description: Optional[str] = None
    schema: Optional[str] = None  # Database schema (e.g., 'public')

@dataclass
class SchemaDefinition:
    """Collection of available tables/views in the datasource."""
    tables: List[TableDefinition] = field(default_factory=list)

@dataclass
class ExecutionResult:
    """
    Standardized result from an adapter execution.
    """
    rows: List[Dict[str, Any]]
    column_names: List[str]
    row_count: int
    data_scanned_bytes: Optional[int] = None
    execution_time_ms: Optional[int] = None
    error: Optional[str] = None

@dataclass
class EstimationResult:
    """
    Result of a pre-flight dry run / cost estimation.
    """
    estimated_row_count: int
    estimated_cost_usd: float = 0.0
    will_succeed: bool = True
    reason: Optional[str] = None
