from typing import List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field


class ColumnRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expr: str
    alias: Optional[str] = None
    is_derived: bool = False


class TableRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    alias: str


class JoinSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left: str
    right: str
    on: List[str]
    join_type: Literal["inner", "left", "right", "full"]


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: ColumnRef
    op: str
    value: str | int | float | bool
    logic: Optional[Literal["and", "or"]] = None


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


class GroupBySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expr: str


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_ids: List[str] = Field(
        description="Entity IDs from the Intent node that this plan satisfies."
    )

    tables: List[TableRef] = Field(default_factory=list)
    joins: List[JoinSpec] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)

    group_by: List[GroupBySpec] = Field(default_factory=list)
    having: List[HavingSpec] = Field(default_factory=list)
    order_by: List[OrderSpec] = Field(default_factory=list)

    limit: Optional[int] = None

    select_columns: List[ColumnRef] = Field(default_factory=list)

    reasoning: Optional[str] = None

    query_type: Literal["READ", "WRITE", "DDL", "UNKNOWN"] = Field("READ")
