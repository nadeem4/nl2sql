from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
from nl2sql_adapter_sdk import Table

class SubQuery(BaseModel):
    query: str = Field(
        description="Natural language question to be executed against the datasource."
    )
    datasource_id: str = Field(
        description="Target datasource for executing this sub-query."
    )
    complexity: Literal["simple", "complex"] = Field(
        default="complex",
        description="Complexity classification."
    )
    relevant_tables: Optional[List[Table]] = Field(
        default=[],
        description="Relevant table schemas for this sub-query. Leave empty."
    )

class DecomposerResponse(BaseModel):
    reasoning: str = Field(
        description="Explanation of why the query was split (or not) and how datasources were selected."
    )
    confidence: float = Field(
        description="Confidence score (0.0 to 1.0) based on vector store matches."
    )
    output_mode: Literal["data", "synthesis"] = Field(
        description="Desired output format: 'data' or 'synthesis'."
    )
    sub_queries: List[SubQuery] = Field(
        description="List of sub-queries (one per datasource)."
    )



