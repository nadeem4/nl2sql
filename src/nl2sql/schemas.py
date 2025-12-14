from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict, Annotated, Union, Set
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




def reduce_thoughts(left: Dict[str, List[str]], right: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Reduces thoughts dictionaries by extending lists for the same key."""
    if not left:
        return right
    if not right:
        return left
    new_thoughts = left.copy()
    for k, v in right.items():
        if k in new_thoughts:
             new_thoughts[k] = new_thoughts[k] + v
        else:
             new_thoughts[k] = v
    return new_thoughts


def merge_dicts(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """Reduces dictionaries by merging them."""
    if not left:
        return right
    if not right:
        return left
    return {**left, **right}


def merge_ids_set(left: Optional[Set[str]], right: Optional[Set[str]]) -> Set[str]:
    """Reduces datasource IDs by unioning them into a set."""
    left = left or set()
    if isinstance(left, list):
        left = set(left)
        
    if right is None:
        return left
    
    if isinstance(right, list):
        right = set(right)
        
    return left | right

class GraphState(BaseModel):
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)
    
    user_query: str
    plan: Optional[Dict[str, Any]] = None
    sql_draft: Optional[str] = None
    schema_info: Optional[SchemaInfo] = None
    validation: Dict[str, Any] = Field(default_factory=dict)
    intent: Optional[IntentModel] = None
    execution: Optional[ExecutionModel] = None
    retrieved_tables: Optional[List[str]] = None
    errors: List[str] = Field(default_factory=list)
    retry_count: int = 0
    thoughts: Annotated[Dict[str, List[str]], reduce_thoughts] = Field(default_factory=dict)
    datasource_id: Annotated[Set[str], merge_ids_set] = Field(default_factory=set)
    sub_queries: Optional[List[str]] = None
    intermediate_results: Annotated[List[Any], operator.add] = Field(default_factory=list)
    query_history: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    final_answer: Optional[str] = None
    routing_info: Annotated[Dict[str, RoutingInfo], merge_dicts] = Field(default_factory=dict)
