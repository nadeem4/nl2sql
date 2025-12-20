from typing import List, Literal
from pydantic import BaseModel, Field

class IntentResponse(BaseModel):
    """
    Structured response from the Intent Node.
    """
    canonical_query: str = Field(description="The standardized, canonical form of the user's query.")
    response_type: Literal["tabular", "kpi", "summary"] = Field(
        description=(
            "The intended format of the response: "
            "'tabular' for lists/grids, 'kpi' for single metrics, "
            "'summary' for analysis/judgment/explanation."
        )
    )
    keywords: List[str] = Field(default_factory=list, description="Extracted technical keywords.")
    entities: List[str] = Field(default_factory=list, description="Extracted named entities.")
    synonyms: List[str] = Field(default_factory=list, description="Synonyms for better retrieval recall.")
