from __future__ import annotations

from typing import Any, Dict, List, Optional, Annotated, Literal
import operator

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.executor.schemas import ExecutionModel
from nl2sql.pipeline.nodes.semantic.schemas import SemanticAnalysisResponse
from nl2sql.common.errors import PipelineError


class GraphState(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    user_query: str = Field(description="Canonical user query.")
    complexity: Literal["simple", "complex"] = Field(
        default="complex",
        description="Complexity classification from Decomposer."
    )
    output_mode: Literal["data", "synthesis"] = Field(
        default="synthesis",
        description="Desired output format: 'data' (raw results) or 'synthesis' (natural language answer)."
    )
    plan: Optional[Dict[str, Any]] = Field(default=None)
    sql_draft: Optional[str] = Field(default=None)
    relevant_tables: Optional[List[Table]] = Field(default=None)
    validation: Dict[str, Any] = Field(default_factory=dict)
    execution: Optional[ExecutionModel] = Field(default=None)
    errors: Annotated[List[PipelineError], operator.add] = Field(default_factory=list)
    retry_count: int = Field(default=0)
    reasoning: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    selected_datasource_id: Optional[str] = Field(default=None)
    sub_queries: Optional[List[SubQuery]] = Field(default=None)
    confidence: Optional[float] = Field(
        default=None,
        description="Confidence score from Decomposer."
    )
    intermediate_results: Annotated[List[Any], operator.add] = Field(default_factory=list)
    query_history: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    final_answer: Optional[str] = Field(default=None)
    final_answer: Optional[str] = Field(default=None)
    system_events: Annotated[List[str], operator.add] = Field(default_factory=list)
    user_context: Optional[Dict[str, Any]] = Field(default_factory=dict, description="User identity and permissions context.")
    semantic_analysis: Optional[SemanticAnalysisResponse] = Field(default=None, description="Enriched query metadata (canonical form, keywords).")
