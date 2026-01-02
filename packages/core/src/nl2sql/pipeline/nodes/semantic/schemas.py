from typing import List, Optional
from pydantic import BaseModel, Field


class SemanticAnalysisResponse(BaseModel):
    """Output for the Semantic Analysis Node.

    Attributes:
        canonical_query (str): The normalized query string.
        keywords (List[str]): Domain-specific keywords extracted from the query.
        synonyms (List[str]): Synonyms for the extracted keywords.
        reasoning (str): Reasoning behind the analysis.
    """
    
    canonical_query: str = Field(
        ..., 
        description="Rewrite the query to be clearer, explicit, and remove conversational filler. Preserve specific constraints."
    )
    keywords: List[str] = Field(
        default_factory=list,
        description="Domain-specific keywords, business terms, or object names relevant to the query."
    )
    synonyms: List[str] = Field(
        default_factory=list,
        description="Alternative names or synonyms for the identified entities/keywords."
    )
    reasoning: str = Field(
        ...,
        description="Brief reasoning about the ambiguity or specific terms identified."
    )
