from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict


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


from pydantic import BaseModel, ConfigDict, Field

class SchemaInfo(BaseModel):
    tables: List[str] = Field(default_factory=list)
    columns: Dict[str, List[str]] = Field(default_factory=dict)
    foreign_keys: Dict[str, List[Dict[str, Optional[str]]]] = Field(default_factory=dict)


class IntentModel(BaseModel):
    entities: list[str] = Field(default_factory=list, description="List of named entities extracted from the query")
    filters: list[str] = Field(default_factory=list, description="List of filters or constraints extracted from the query")
    keywords: list[str] = Field(default_factory=list, description="List of technical keywords, synonyms, or likely table names")
    clarifications: list[str] = Field(default_factory=list, description="List of clarifying questions if the query is ambiguous")


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tables: list[Dict[str, Any]] = Field(default_factory=list)
    joins: list[Dict[str, Any]] = Field(default_factory=list)
    filters: list[Dict[str, Any]] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[Dict[str, Any]] = Field(default_factory=list)
    having: list[Dict[str, Any]] = Field(default_factory=list)
    order_by: list[Dict[str, Any]] = Field(default_factory=list)
    limit: Optional[int] = None


@dataclass
class GraphState:
    user_query: str
    plan: Optional[Plan] = None
    sql_draft: Optional[GeneratedSQL] = None
    schema_info: Optional[SchemaInfo] = None
    validation: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=dict)
    retrieved_tables: Optional[List[str]] = None
    errors: List[str] = field(default_factory=list)
    retry_count: int = 0

    def __post_init__(self):
        if isinstance(self.schema_info, dict):
            self.schema_info = SchemaInfo(**self.schema_info)
