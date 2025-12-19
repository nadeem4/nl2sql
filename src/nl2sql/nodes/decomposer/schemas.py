from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class SubQuery(BaseModel):
    query: str = Field(description="The individual sub-query.")
    datasource_id: str = Field(description="The target datasource ID for this query.")
    candidate_tables: Optional[List[str]] = Field(default=None, description="Optional list of table names if known from context.")
    complexity: Literal["simple", "complex"] = Field(default="complex", description="Complexity of the query. 'simple' = direct retrieval/filtering. 'complex' = aggregation, multi-step, or abstract reasoning.")
    reasoning: Optional[str] = Field(default=None, description="Reasoning for this specific split and datasource selection.")

class DecomposerResponse(BaseModel):
    """Structured response for the query decomposer."""
    sub_queries: List[SubQuery] = Field(
        description="List of sub-queries. If no decomposition is needed, this list should contain only the original query (with its determined datasource)."
    )
    reasoning: str = Field(description="Global reasoning for why the query was decomposed (or not).")

class EnrichedIntent(BaseModel):
    """
    Extracted intent information to enrich vector search.
    """
    keywords: List[str] = Field(default_factory=list, description="Technical keywords or table names implied.")
    entities: List[str] = Field(default_factory=list, description="Named entities (people, products, locations).")
    synonyms: List[str] = Field(default_factory=list, description="Synonyms or related domain terms to aid retrieval.")
    complexity: Literal["simple", "complex"] = Field(default="complex", description="Estimated query complexity. 'simple' implies single-table or basic multi-table retrieval.")

