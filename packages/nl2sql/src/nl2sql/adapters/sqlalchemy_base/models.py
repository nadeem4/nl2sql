from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any


class QueryResult(BaseModel):
    """Normalized results from a datasource execution."""
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    raw: Optional[Any] = None
    execution_time_ms: Optional[float] = None
    bytes_returned: Optional[int] = None

class DryRunResult(BaseModel):
    """Result of a query validation/dry-run."""
    is_valid: bool
    error_message: Optional[str] = None
    data: Optional[Any] = None

class QueryPlan(BaseModel):
    """Structure representing a database execution plan."""
    plan_text: str
    format: str = "text" # or 'json', 'xml'

class CostEstimate(BaseModel):
    """Estimated resource usage for a query."""
    estimated_cost: float
    estimated_rows: int
    estimated_time_ms: Optional[float] = None

class AdapterError(BaseModel):
    """Standardized error envelope for adapter failures."""
    code: str
    message: str
    retriable: bool
    raw: Optional[Any] = None
