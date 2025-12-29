from __future__ import annotations

from typing import Any, Dict, List, Optional, Annotated, Literal
import operator

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.pipeline.nodes.schema.schemas import SchemaInfo, TableInfo
from nl2sql.pipeline.nodes.executor.schemas import ExecutionModel
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery, EntityMapping
from nl2sql.pipeline.nodes.intent.schemas import IntentResponse, Entity
from nl2sql.pipeline.nodes.planner.schemas import PlanModel, TableRef, ColumnRef, HavingSpec
from nl2sql.common.errors import PipelineError


class GraphState(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)

    user_query: str = Field(description="Canonical user query.")
    plan: Optional[Dict[str, Any]] = Field(default=None)
    sql_draft: Optional[str] = Field(default=None)
    schema_info: Optional[SchemaInfo] = Field(default=None)

    validation: Dict[str, Any] = Field(default_factory=dict)

    execution: Optional[ExecutionModel] = Field(default=None)

    errors: Annotated[List[PipelineError], operator.add] = Field(default_factory=list)
    retry_count: int = Field(default=0)

    reasoning: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)

    selected_datasource_id: Optional[str] = Field(default=None)

    sub_queries: Optional[List[SubQuery]] = Field(default=None)

    entities: Optional[List[Entity]] = Field(
        default=None,
        description="Authoritative entity graph from Intent node."
    )

    entity_mapping: Optional[List[EntityMapping]] = Field(
        default=None,
        description="Entity-to-datasource mapping from Decomposer."
    )

    confidence: Optional[float] = Field(
        default=None,
        description="Confidence score from Decomposer."
    )

    analysis_intent: Optional[
        Literal["lookup", "aggregation", "comparison", "trend", "diagnostic", "validation"]
    ] = Field(default=None)

    ambiguity_level: Optional[
        Literal["low", "medium", "high"]
    ] = Field(default=None)

    response_type: Literal["tabular", "kpi", "summary"] = Field(default="tabular")

    enriched_terms: Annotated[List[str], operator.add] = Field(default_factory=list)

    intermediate_results: Annotated[List[Any], operator.add] = Field(default_factory=list)

    query_history: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)

    final_answer: Optional[str] = Field(default=None)

    system_events: Annotated[List[str], operator.add] = Field(default_factory=list)
