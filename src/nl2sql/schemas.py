from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict, Annotated, Union
import operator

from pydantic import BaseModel, ConfigDict, Field

# Re-export node schemas
from nl2sql.nodes.schema.schemas import TableInfo, SchemaInfo, ForeignKey
from nl2sql.nodes.planner.schemas import (
    PlanModel, TableRef, JoinSpec, FilterSpec, ColumnRef, 
    HavingSpec, OrderSpec
)
from nl2sql.nodes.intent.schemas import IntentModel
from nl2sql.nodes.executor.schemas import ExecutionModel

from nl2sql.nodes.decomposer.schemas import DecomposerResponse
from nl2sql.nodes.aggregator.schemas import AggregatedResponse
from nl2sql.nodes.router.schemas import RoutingInfo


def reduce_latency(left: Dict[str, float], right: Dict[str, float]) -> Dict[str, float]:
    """Reduces latency dictionaries by summing values for the same key."""
    if not left:
        return right
    if not right:
        return left
    new_latency = left.copy()
    for k, v in right.items():
        new_latency[k] = new_latency.get(k, 0.0) + v
    return new_latency


def merge_dicts(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """Reduces dictionaries by merging them."""
    if not left:
        return right
    if not right:
        return left
    return {**left, **right}


def merge_ids(left: Any, right: Any) -> Any:
    """Reduces datasource IDs by merging them into a list."""
    if not left:
        return right
    if not right:
        return left
    
    l_list = left if isinstance(left, list) else [left]
    r_list = right if isinstance(right, list) else [right]
    
    res = list(set(l_list + r_list))
    return res[0] if len(res) == 1 else res


class GraphState(BaseModel):
    """
    Represents the state of the LangGraph execution.

    Attributes:
        user_query: The original user query.
        plan: The generated execution plan (as a dict).
        sql_draft: The generated SQL draft.
        schema_info: The retrieved schema information.
        validation: Validation results and metadata.
        execution: Execution results.
        retrieved_tables: List of tables retrieved from vector store.
        latency: Latency metrics for each step.
        errors: List of errors encountered during execution.
        retry_count: Number of retries attempted.
        thoughts: Chain of thought logs from each node.
        datasource_id: The ID of the selected datasource.
        sub_queries: List of sub-queries for cross-db execution.
        intermediate_results: List of results from sub-queries.
    """
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)
    
    user_query: str
    plan: Optional[Dict[str, Any]] = None
    sql_draft: Optional[str] = None
    schema_info: Optional[SchemaInfo] = None
    validation: Dict[str, Any] = Field(default_factory=dict)
    intent: Optional[IntentModel] = None
    execution: Optional[ExecutionModel] = None
    retrieved_tables: Optional[List[str]] = None
    latency: Annotated[Dict[str, float], reduce_latency] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    retry_count: int = 0
    thoughts: Dict[str, List[str]] = Field(default_factory=dict)
    datasource_id: Annotated[Optional[Union[str, List[str]]], merge_ids] = None
    sub_queries: Optional[List[str]] = None
    intermediate_results: Annotated[List[Any], operator.add] = Field(default_factory=list)
    query_history: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    final_answer: Optional[str] = None
    routing_info: Annotated[Dict[str, RoutingInfo], merge_dicts] = Field(default_factory=dict)
