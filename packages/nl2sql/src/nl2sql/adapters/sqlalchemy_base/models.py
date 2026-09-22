from pydantic import BaseModel
from typing import Optional, Any

# Results and errors use the SDK's ResultFrame and ResultError
# (nl2sql_adapter_sdk.contracts); these are the SQL-only extras.


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
