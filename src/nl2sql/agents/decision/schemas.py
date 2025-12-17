from typing import Optional
from pydantic import BaseModel, Field

class RouterDecision(BaseModel):
    """Represents the decision made by the reasoning router agent."""
    datasource_id: Optional[str] = Field(description="The ID of the selected database. Return None if no database matches the query intent.")
    reasoning: str = Field(description="The step-by-step logical reasoning explaining why this datasource was chosen (or why none matched).")
