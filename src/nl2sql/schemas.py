from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, TypedDict


class TableRef(TypedDict):
    name: str
    alias: Optional[str]


class JoinSpec(TypedDict):
    left: str
    right: str
    on: List[str]  # e.g., ["left.alias = right.alias"]
    join_type: Literal["inner", "left", "right", "full"]


class FilterSpec(TypedDict, total=False):
    column: str
    op: str
    value: str | int | float | bool
    logic: Literal["and", "or"]


class AggregateSpec(TypedDict):
    expr: str
    alias: Optional[str]


class OrderSpec(TypedDict):
    expr: str
    direction: Literal["asc", "desc"]


class Plan(TypedDict, total=False):
    tables: List[TableRef]
    joins: List[JoinSpec]
    filters: List[FilterSpec]
    group_by: List[str]
    aggregates: List[AggregateSpec]
    having: List[FilterSpec]
    order_by: List[OrderSpec]
    limit: int


class GeneratedSQL(TypedDict):
    sql: str
    rationale: str
    limit_enforced: bool
    draft_only: bool


@dataclass
class GraphState:
    user_query: str
    plan: Optional[Plan] = None
    sql_draft: Optional[GeneratedSQL] = None
    validation: Dict[str, str] = field(default_factory=dict)
    execution: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
