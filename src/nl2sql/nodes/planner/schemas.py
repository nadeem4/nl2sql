from typing import List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field

class ColumnRef(BaseModel):
    """
    Represents a reference to a column or an expression in the query plan.

    Attributes:
        expr: The full expression (e.g., "t1.name" or "COUNT(*)").
        alias: The output column alias (e.g., "total_orders"). Only used in select_columns.
        is_derived: True if expr is a derived expression (aggregation, function), False otherwise.
    """
    model_config = ConfigDict(extra="forbid")
    expr: str
    alias: Optional[str] = None
    is_derived: bool = False


class TableRef(BaseModel):
    """
    Represents a table reference in the execution plan.

    Attributes:
        name: The name of the table.
        alias: The alias used for the table in the query.
    """
    model_config = ConfigDict(extra="forbid")
    name: str
    alias: str


class JoinSpec(BaseModel):
    """
    Represents a join operation between two tables.

    Attributes:
        left: The left table name or alias.
        right: The right table name or alias.
        on: List of join conditions (e.g., ["t1.id = t2.user_id"]).
        join_type: The type of join (inner, left, right, full).
    """
    model_config = ConfigDict(extra="forbid")
    left: str
    right: str
    on: List[str]
    join_type: Literal["inner", "left", "right", "full"]


class FilterSpec(BaseModel):
    """
    Represents a filter condition (WHERE clause).

    Attributes:
        column: The column or expression being filtered.
        op: The operator (e.g., "=", ">", "LIKE").
        value: The value to compare against.
        logic: Logical operator to combine with previous filters (and/or).
    """
    model_config = ConfigDict(extra="forbid")
    column: ColumnRef
    op: str
    value: str | int | float | bool
    logic: Optional[Literal["and", "or"]] = None





class HavingSpec(BaseModel):
    """
    Represents a HAVING clause condition.

    Attributes:
        expr: The expression being filtered (usually an aggregation).
        op: The operator.
        value: The value to compare against.
        logic: Logical operator (and/or).
    """
    model_config = ConfigDict(extra="forbid")
    expr: str
    op: str
    value: str | int | float | bool
    logic: Optional[Literal["and", "or"]] = None


class OrderSpec(BaseModel):
    """
    Represents an ORDER BY clause.

    Attributes:
        column: The column to sort by.
        direction: Sort direction ("asc" or "desc").
    """
    model_config = ConfigDict(extra="forbid")
    column: ColumnRef
    direction: Literal["asc", "desc"]


class PlanModel(BaseModel):
    """
    Represents the structured execution plan for the SQL query.

    Attributes:
        tables: List of tables involved in the query.
        joins: List of join operations.
        filters: List of WHERE clause filters.
        group_by: List of columns to group by.
        having: List of HAVING clause conditions.
        order_by: List of sorting specifications.
        limit: Row limit.
        select_columns: List of columns to select (including aggregations).
        reasoning: Reasoning for the plan choices.
        query_type: The type of query (READ, WRITE, DDL).
    """
    model_config = ConfigDict(extra="forbid")
    tables: list[TableRef] = Field(default_factory=list)
    joins: list[JoinSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    group_by: list[ColumnRef] = Field(default_factory=list)
    having: list[HavingSpec] = Field(default_factory=list)
    order_by: list[OrderSpec] = Field(default_factory=list)
    limit: Optional[int] = None
    select_columns: list[ColumnRef] = Field(default_factory=list)
    reasoning: Optional[str] = Field(None)
    query_type: Literal["READ", "WRITE", "DDL", "UNKNOWN"] = Field("READ")
