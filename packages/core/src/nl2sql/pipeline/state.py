from __future__ import annotations

from typing import Any, Dict, List, Optional, Annotated, Literal
import operator
import uuid

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.nodes.planner.schemas import PlanModel
from nl2sql.pipeline.nodes.executor.schemas import ExecutionModel
from nl2sql.pipeline.nodes.semantic.schemas import SemanticAnalysisResponse
from nl2sql.common.errors import PipelineError
from nl2sql_adapter_sdk import Table


class GraphState(BaseModel):
    """Represents the shared state of the NL2SQL pipeline execution graph.

    Attributes:
        user_query (str): Canonical user query with filler removed.
        complexity (Literal): Complexity classification from Decomposer (simple/complex).
        output_mode (Literal): Desired output format (data/synthesis).
        plan (Optional[PlanModel]): Structured SQL plan from Planner.
        sql_draft (Optional[str]): Generated SQL query string.
        relevant_tables (Optional[List[Table]]): List of table schemas relevant to the query.
        validation (Dict[str, Any]): Validation results and metadata.
        execution (Optional[ExecutionModel]): Execution results from the database.
        errors (List[PipelineError]): List of errors encountered during execution.
        retry_count (int): Number of retries attempted.
        reasoning (List[Dict[str, Any]]): Log of reasoning steps from nodes.
        selected_datasource_id (Optional[str]): ID of the target datasource.
        sub_queries (Optional[List[SubQuery]]): List of decomposed sub-queries.
        confidence (Optional[float]): Confidence score from Decomposer.
        intermediate_results (List[Any]): Results from sub-query execution branches.
        query_history (List[Dict[str, Any]]): History of executed sub-queries.
        final_answer (Optional[str]): The synthesized final response for the user.
        system_events (List[str]): Log of distinct system events.
        user_context (Optional[Dict[str, Any]]): User identity and permissions context.
        semantic_analysis (Optional[SemanticAnalysisResponse]): Enriched query metadata.
    """
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Distributed unique trace ID.")
    user_query: str = Field(description="Canonical user query.")
    complexity: Literal["simple", "complex"] = Field(
        default="complex",
        description="Complexity classification from Decomposer."
    )
    output_mode: Literal["data", "synthesis"] = Field(
        default="synthesis",
        description="Desired output format: 'data' (raw results) or 'synthesis' (natural language answer)."
    )
    plan: Optional[PlanModel] = Field(default=None)
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
    system_events: Annotated[List[str], operator.add] = Field(default_factory=list)
    user_context: Optional[Dict[str, Any]] = Field(default_factory=dict, description="User identity and permissions context.")
    semantic_analysis: Optional[SemanticAnalysisResponse] = Field(default=None, description="Enriched query metadata (canonical form, keywords).")
