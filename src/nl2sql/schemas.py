from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class ColumnRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str
    name: str


class ForeignKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    referred_table: str
    referred_column: str


class TableInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    alias: str
    columns: List[str]
    foreign_keys: List[ForeignKey] = Field(default_factory=list)


class TableRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    alias: str  # Mandatory now


class JoinSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: str
    right: str
    on: List[str]  # e.g., ["left.alias = right.alias"]
    join_type: Literal["inner", "left", "right", "full"]


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: ColumnRef
    op: str
    value: str | int | float | bool
    logic: Optional[Literal["and", "or"]] = None


class AggregateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expr: str
    alias: Optional[str] = None


class HavingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expr: str
    op: str
    value: str | int | float | bool
    logic: Optional[Literal["and", "or"]] = None


class OrderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: ColumnRef
    direction: Literal["asc", "desc"]


class GeneratedSQL(TypedDict):
    sql: str
    rationale: str
    limit_enforced: bool
    draft_only: bool


class SchemaInfo(BaseModel):
    tables: List[TableInfo] = Field(default_factory=list)


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
    group_by: list[ColumnRef] = Field(default_factory=list)
    aggregates: list[AggregateSpec] = Field(default_factory=list)
    having: list[HavingSpec] = Field(default_factory=list)
    order_by: list[OrderSpec] = Field(default_factory=list)
    limit: Optional[int] = None
    select_columns: list[ColumnRef] = Field(default_factory=list, description="List of columns to be selected in the final result.")
    reasoning: Optional[str] = Field(None, description="Step-by-step reasoning for the plan choices")


@dataclass
class GraphState:
    user_query: str
    plan: Optional[Dict[str, Any]] = None  # Stores PlanModel.model_dump()
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
