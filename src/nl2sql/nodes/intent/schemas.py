from typing import Optional, Literal
from pydantic import BaseModel, Field, ConfigDict

class IntentModel(BaseModel):
    """
    Represents the analyzed intent of the user query.

    Attributes:
        entities: Named entities extracted from the query.
        filters: Filters or constraints extracted from the query.
        keywords: Technical keywords or table names.
        clarifications: Clarifying questions if ambiguous.
        reasoning: Step-by-step reasoning for intent classification.
        query_expansion: Synonyms and related terms.
        query_type: Classification of the query intent (READ, WRITE, DDL).
    """
    model_config = ConfigDict(extra="forbid")
    entities: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list)
    reasoning: Optional[str] = Field(None)
    query_expansion: list[str] = Field(default_factory=list)
    query_type: Literal["READ", "WRITE", "DDL", "UNKNOWN"] = Field("READ")
