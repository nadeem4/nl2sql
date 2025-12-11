from typing import List
from pydantic import BaseModel, Field

class DecomposerResponse(BaseModel):
    """Structured response for the query decomposer."""
    sub_queries: List[str] = Field(
        description="List of sub-queries. If no decomposition is needed, this list should contain only the original query."
    )
    reasoning: str = Field(description="Reasoning for why the query was decomposed (or not).")
