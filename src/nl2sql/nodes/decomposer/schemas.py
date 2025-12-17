from typing import List, Optional
from pydantic import BaseModel, Field

class SubQueryResponse(BaseModel):
    query: str = Field(description="The individual sub-query.")
    datasource_id: str = Field(description="The target datasource ID for this query.")
    candidate_tables: Optional[List[str]] = Field(default=None, description="Optional list of table names if known from context.")
    reasoning: Optional[str] = Field(description="Reasoning for this specific split and datasource selection.")

class DecomposerResponse(BaseModel):
    """Structured response for the query decomposer."""
    sub_queries: List[SubQueryResponse] = Field(
        description="List of sub-queries. If no decomposition is needed, this list should contain only the original query (with its determined datasource)."
    )
    reasoning: str = Field(description="Global reasoning for why the query was decomposed (or not).")
