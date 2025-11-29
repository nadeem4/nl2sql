from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class TableRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    alias: Optional[str] = None


class JoinSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: str
    right: str
    on: List[str]  # e.g., ["left.alias = right.alias"]
    join_type: Literal["inner", "left", "right", "full"]


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    op: str
    value: str | int | float | bool
    logic: Optional[Literal["and", "or"]] = None


class AggregateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expr: str
    alias: Optional[str] = None


class OrderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expr: str
    direction: Literal["asc", "desc"]


class Plan(TypedDict, total=False):
    # Keep Plan as TypedDict for backward compatibility if needed, 
    # but PlanModel is the one used for LLM. 
    # Actually, let's just update PlanModel to use the new classes.
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


class SchemaInfo(BaseModel):
    tables: List[str] = Field(default_factory=list)
    columns: Dict[str, List[str]] = Field(default_factory=dict)
    foreign_keys: Dict[str, List[Dict[str, Optional[str]]]] = Field(default_factory=dict)


class IntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[str] = Field(default_factory=list, description="List of named entities extracted from the query")
    filters: list[str] = Field(default_factory=list, description="List of filters or constraints extracted from the query")
    keywords: list[str] = Field(default_factory=list, description="List of technical keywords, synonyms, or likely table names")
    clarifications: list[str] = Field(default_factory=list, description="List of clarifying questions if the query is ambiguous")


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tables: list[TableRef] = Field(default_factory=list)
    joins: list[JoinSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[AggregateSpec] = Field(default_factory=list)
    having: list[FilterSpec] = Field(default_factory=list)
    order_by: list[OrderSpec] = Field(default_factory=list)
    limit: Optional[int] = None
    needed_columns: list[str] = Field(default_factory=list, description="List of all columns referenced in the plan (e.g., 'table.column')")
    reasoning: Optional[str] = Field(None, description="Step-by-step reasoning for the plan choices")


@dataclass
class GraphState:
    user_query: str
    plan: Optional[Plan] = None
    sql_draft: Optional[GeneratedSQL] = None
    schema_info: Optional[SchemaInfo] = None
    validation: Dict[str, Any] = field(default_factory=dict)
    execution: Dict[str, Any] = field(default_factory=dict)
    retrieved_tables: Optional[List[str]] = None
    latency: Dict[str, float] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    retry_count: int = 0

    def __post_init__(self):
        if isinstance(self.schema_info, dict):
            self.schema_info = SchemaInfo(**self.schema_info)


class SQLModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str
    rationale: Optional[str] = None
    limit_enforced: Optional[bool] = None
    draft_only: Optional[bool] = None
