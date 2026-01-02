from __future__ import annotations
from typing import List, Optional, Literal, Union
from pydantic import BaseModel, ConfigDict, Field


# =========================
# CASE WHEN SUPPORT
# =========================

class CaseWhen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: "Expr"
    result: "Expr"
    ordinal: int


# =========================
# Unified Expression AST
# =========================

class Expr(BaseModel):
    """
    Unified AST for deterministic SQL.
    'kind' determines which fields must / may be populated.
    Strict validation rules enforced in model_post_init.
    """
    model_config = ConfigDict(extra="forbid")

    kind: Literal["literal", "column", "func", "binary", "unary", "case"]

    # LITERAL
    value: Optional[Union[str, int, float, bool]] = None
    is_null: bool = False

    # COLUMN
    alias: Optional[str] = None
    column_name: Optional[str] = None

    # FUNCTION
    func_name: Optional[str] = None
    args: List["Expr"] = Field(default_factory=list)
    is_aggregate: bool = False

    # OPERATORS
    op: Optional[
        Literal[
            "=", "!=", ">", "<", ">=", "<=",
            "+", "-", "*", "/", "%",
            "AND", "OR", "LIKE", "IN",
            "NOT"
        ]
    ] = None

    left: Optional["Expr"] = None
    right: Optional["Expr"] = None

    # UNARY
    expr: Optional["Expr"] = None

    # CASE
    whens: List[CaseWhen] = Field(default_factory=list)
    else_expr: Optional["Expr"] = None


    # =========================
    # Semantic Deterministic Validation
    # =========================
    def model_post_init(self, *_):
        k = self.kind

        if k == "literal":
            if self.value is None and not self.is_null:
                raise ValueError("Literal must have value or is_null=True")

        if k == "column":
            if not self.column_name:
                raise ValueError("column_name is required for column expression")

        if k == "func":
            if not self.func_name:
                raise ValueError("func_name is required for func expression")

        if k == "binary":
            if not (self.left and self.right):
                raise ValueError("Binary expression requires left and right")
            if not self.op:
                raise ValueError("Binary expression requires operator")

        if k == "unary":
            if not self.expr:
                raise ValueError("Unary expression requires expr")
            if self.op not in ("NOT", "+", "-"):
                raise ValueError("Unary op must be NOT, +, or -")

        if k == "case":
            if not self.whens or len(self.whens) == 0:
                raise ValueError("CASE expression must have at least one WHEN")


# =========================
# Relational Structure
# =========================

class TableRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    schema_name: Optional[str] = None
    database: Optional[str] = None
    alias: str
    ordinal: int


class JoinSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_alias: str
    right_alias: str
    join_type: Literal["inner", "left", "right", "full"] = "inner"
    condition: Expr
    ordinal: int


# =========================
# Projection / Grouping / Ordering
# =========================

class SelectItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expr: Expr
    alias: Optional[str] = None
    ordinal: int


class OrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expr: Expr
    direction: Literal["asc", "desc"] = "asc"
    ordinal: int


class GroupByItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expr: Expr
    ordinal: int


# =========================
# PLAN MODEL
# =========================

class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_type: Literal["READ"] = "READ"
    distinct: bool = False

    tables: List[TableRef] = Field(default_factory=list)
    joins: List[JoinSpec] = Field(default_factory=list)

    select_items: List[SelectItem] = Field(default_factory=list)

    where: Optional[Expr] = None
    group_by: List[GroupByItem] = Field(default_factory=list)
    having: Optional[Expr] = None
    order_by: List[OrderItem] = Field(default_factory=list)

    limit: Optional[int] = None
    offset: Optional[int] = None

    reasoning: Optional[str] = None


# Fix forward references
Expr.model_rebuild()
CaseWhen.model_rebuild()
PlanModel.model_rebuild()
