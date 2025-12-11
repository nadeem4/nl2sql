from typing import List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict, Field

class ExecutionModel(BaseModel):
    """
    Represents the result of an SQL execution.
    """
    row_count: int = Field(description="Number of rows returned")
    rows: List[Dict[str, Any]] = Field(default_factory=list, description="The actual data rows")
    columns: List[str] = Field(default_factory=list, description="Column names")
    error: Optional[str] = Field(None, description="Error message if execution failed")

    model_config = ConfigDict(extra="allow")
