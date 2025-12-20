from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class EntityMapping(BaseModel):
    entity_id: str = Field(description="Stable entity ID from the Intent node.")
    datasource_id: str = Field(description="Datasource assigned based on schema coverage.")
    candidate_tables: Optional[List[str]] = Field(
        default=None,
        description="Tables providing physical coverage for this entity."
    )
    coverage_reasoning: str = Field(
        description="Why this datasource was selected for the entity."
    )


class SubQuery(BaseModel):
    entity_ids: List[str] = Field(
        description="Entity IDs covered by this sub-query."
    )
    query: str = Field(
        description="Natural language question scoped strictly to the listed entity IDs."
    )
    datasource_id: str = Field(
        description="Target datasource for executing this sub-query."
    )
    complexity: Literal["simple", "complex"] = Field(
        default="complex",
        description="Complexity classification."
    )


class DecomposerResponse(BaseModel):
    reasoning: str = Field(
        description="Coverage-based explanation referencing entity IDs."
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence score based on schema coverage and ambiguity."
    )
    entity_mapping: List[EntityMapping] = Field(
        description="Mapping of each entity ID to its assigned datasource."
    )
    sub_queries: List[SubQuery] = Field(
        description="One sub-query per datasource group."
    )



