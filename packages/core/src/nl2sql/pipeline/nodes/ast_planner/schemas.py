from __future__ import annotations
from typing import List, Optional, Literal, Union
from pydantic import BaseModel, ConfigDict, Field


class CaseWhen(BaseModel):
    """Represents a single WHEN ... THEN ... clause in a CASE expression.

    Attributes:
        condition (Expr): The condition expression to evaluate.
        result (Expr): The result expression if the condition is true.
        ordinal (int): The position of this clause in the CASE statement.
    """
    model_config = ConfigDict(extra="forbid")

    condition: "Expr"
    result: "Expr"
    ordinal: int


class Expr(BaseModel):
    """Unified AST for deterministic SQL expressions.

    The 'kind' attribute determines which fields must or may be populated.
    Strict validation rules are enforced in `model_post_init`.

    Attributes:
        kind (Literal): The type of expression (literal, column, func, binary, unary, case).
        value (Optional[Union[str, int, float, bool]]): Value for literal expressions.
        is_null (bool): Whether the literal is a NULL value.
        alias (Optional[str]): Table alias for column expressions.
        column_name (Optional[str]): Name of the column.
        func_name (Optional[str]): Name of the function.
        args (List[Expr]): Arguments for function expressions.
        is_aggregate (bool): Whether the function is an aggregate function.
        op (Optional[str]): Operator for binary or unary expressions.
        left (Optional[Expr]): Left operand for binary expressions.
        right (Optional[Expr]): Right operand for binary expressions.
        expr (Optional[Expr]): Operand for unary expressions.
        whens (List[CaseWhen]): List of WHEN clauses for CASE expressions.
        else_expr (Optional[Expr]): The ELSE expression for CASE expressions.
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
            "NOT", "IS", "IS NOT"
        ]
    ] = None

    left: Optional["Expr"] = None
    right: Optional["Expr"] = None

    # UNARY
    expr: Optional["Expr"] = None

    # CASE
    whens: List[CaseWhen] = Field(default_factory=list)
    else_expr: Optional["Expr"] = None

    def model_post_init(self, *_):
        """Validates the expression based on its `kind`."""
        k = self.kind

        if k == "literal":
            if self.value is None and not self.is_null:
                raise ValueError("Literal must have value or is_null=True")

        if k == "column" and not self.column_name:
            raise ValueError("column_name is required for column expression")

        if k == "func" and not self.func_name:
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

        if k == "case" and not self.whens:
            raise ValueError("CASE expression must have at least one WHEN")


class TableRef(BaseModel):
    """Represents a reference to a database table.

    Attributes:
        name (str): The name of the table.
        schema_name (Optional[str]): The schema of the table.
        database (Optional[str]): The database name.
        alias (str): The alias used for the table in the query.
        ordinal (int): The strict ordinal position of the table.
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    schema_name: Optional[str] = None
    database: Optional[str] = None
    alias: str
    ordinal: int


class JoinSpec(BaseModel):
    """Specification for a table join.

    Attributes:
        left_alias (str): Alias of the left table.
        right_alias (str): Alias of the right table.
        join_type (Literal): Type of join (inner, left, right, full).
        condition (Expr): The join condition expression.
        ordinal (int): The strict ordinal position of the join.
    """
    model_config = ConfigDict(extra="forbid")

    left_alias: str
    right_alias: str
    join_type: Literal["inner", "left", "right", "full"] = "inner"
    condition: Expr
    ordinal: int


class SelectItem(BaseModel):
    """Represents an item in the SELECT clause.

    Attributes:
        expr (Expr): The expression to select.
        alias (Optional[str]): The alias for the selected expression.
        ordinal (int): The strict ordinal position of the select item.
    """
    model_config = ConfigDict(extra="forbid")

    expr: Expr
    alias: Optional[str] = None
    ordinal: int


class OrderItem(BaseModel):
    """Represents an item in the ORDER BY clause.

    Attributes:
        expr (Expr): The expression to order by.
        direction (Literal): The sort direction (asc, desc).
        ordinal (int): The strict ordinal position of the order item.
    """
    model_config = ConfigDict(extra="forbid")

    expr: Expr
    direction: Literal["asc", "desc"] = "asc"
    ordinal: int


class GroupByItem(BaseModel):
    """Represents an item in the GROUP BY clause.

    Attributes:
        expr (Expr): The expression to group by.
        ordinal (int): The strict ordinal position of the group by item.
    """
    model_config = ConfigDict(extra="forbid")

    expr: Expr
    ordinal: int


class PlanModel(BaseModel):
    """Standardized representation of a SQL execution plan.

    Attributes:
        query_type (Literal): The type of query (default: READ).
        distinct (bool): Whether to select distinct rows.
        tables (List[TableRef]): List of tables involved in the query.
        joins (List[JoinSpec]): List of join specifications.
        select_items (List[SelectItem]): List of items to select.
        where (Optional[Expr]): The WHERE clause expression.
        group_by (List[GroupByItem]): List of GROUP BY items.
        having (Optional[Expr]): The HAVING clause expression.
        order_by (List[OrderItem]): List of ORDER BY items.
        limit (Optional[int]): The LIMIT count.
        offset (Optional[int]): The OFFSET count.
        reasoning (Optional[str]): Explanatory text for the plan generation.
    """
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


class ASTPlannerResponse(BaseModel):
    plan: Optional[PlanModel] = None


# Fix forward references
Expr.model_rebuild()
CaseWhen.model_rebuild()
PlanModel.model_rebuild()
