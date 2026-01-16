from typing import List, Optional, Literal, Dict, Any, Union
from pydantic import BaseModel, Field
from nl2sql_adapter_sdk import Table


class ColumnDefinition(BaseModel):
    name: str = Field(description="Name of the column")
    description: Optional[str] = Field(description="Description of what this column represents")


class SubQuery(BaseModel):
    """Represents a decomposed query targeting a specific datasource.

    Attributes:
        query (str): Natural language question to be executed against the datasource.
        datasource_id (str): Target datasource for executing this sub-query.
        complexity (Literal): Complexity classification (simple or complex).
        relevant_tables (Optional[List[Table]]): Relevant table schemas for this sub-query.
        expected_schema (List[ColumnDefinition]): The columns that this sub-query MUST return.
    """
    id: str = Field(
        description="Unique identifier for this sub-query."
    )
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
    expected_schema: Optional[List[ColumnDefinition]] = Field(
        default=None,
        description="The columns that this sub-query MUST return for the aggregation to work (e.g. Join Keys)."
    )





class DecomposerResponse(BaseModel):
    """Structured response from the Decomposer LLM.

    Attributes:
        reasoning (str): Explanation of the routing decision.
        confidence (float): Confidence score (0.0 to 1.0).
        output_mode (Literal): Desired output format (data or synthesis).
        sub_queries (List[SubQuery]): List of sub-queries generated.
        result_plan (Optional[ResultPlan]): How to merge the results.
    """
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
