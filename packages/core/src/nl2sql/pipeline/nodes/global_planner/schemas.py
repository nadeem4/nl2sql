from __future__ import annotations
from typing import List, Literal, Union, Optional
from pydantic import BaseModel, Field, model_validator

# -----------------------------
# JSON-safe literal definition
# -----------------------------

JsonLiteral = Union[str, int, float, bool, None]

# -----------------------------
# Core relation + schema types
# -----------------------------

class RelationRef(BaseModel):
    id: str = Field(description="SubQuery.id or PlanStep.id")
    source: Literal["subquery", "step"]

class ColumnSpec(BaseModel):
    name: str
    dtype: Optional[str] = None
    nullable: bool = True
    description: Optional[str] = None

class RelationSchema(BaseModel):
    columns: List[ColumnSpec]

    @model_validator(mode="after")
    def validate_unique_columns(self):
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate columns in schema: {names}")
        return self

# -----------------------------
# Typed expressions (NO SQL)
# -----------------------------

class Expr(BaseModel):
    pass

class Col(Expr):
    type: Literal["col"] = "col"
    name: str

class Lit(Expr):
    type: Literal["lit"] = "lit"
    value: JsonLiteral

class BinOp(Expr):
    type: Literal["binop"] = "binop"
    op: Literal["=", "!=", ">", ">=", "<", "<=", "and", "or"]
    left: Expr
    right: Expr

class AggSpec(BaseModel):
    func: Literal["count", "sum", "avg", "min", "max"]
    expr: Expr
    distinct: bool = False
    as_name: str

class SortSpec(BaseModel):
    expr: Expr
    direction: Literal["asc", "desc"] = "asc"
    nulls: Literal["first", "last"] = "last"

# -----------------------------
# Relational operations
# -----------------------------

class Operation(BaseModel):
    op: str

class ProjectOp(Operation):
    op: Literal["project"] = "project"
    input: RelationRef
    exprs: List[Expr]
    aliases: List[str]

class FilterOp(Operation):
    op: Literal["filter"] = "filter"
    input: RelationRef
    predicate: Expr

class JoinOp(Operation):
    op: Literal["join"] = "join"
    left: RelationRef
    right: RelationRef
    join_type: Literal["inner", "left", "right", "full"] = "inner"
    on: List[BinOp]
    suffixes: List[str] = ["_l", "_r"]

class UnionOp(Operation):
    op: Literal["union"] = "union"
    inputs: List[RelationRef]
    mode: Literal["all", "distinct"] = "all"

class GroupAggOp(Operation):
    op: Literal["group_agg"] = "group_agg"
    input: RelationRef
    keys: List[Expr]
    aggs: List[AggSpec]

class OrderLimitOp(Operation):
    op: Literal["order_limit"] = "order_limit"
    input: RelationRef
    order_by: List[SortSpec]
    limit: Optional[int] = None
    offset: Optional[int] = None

OpT = Union[
    ProjectOp,
    FilterOp,
    JoinOp,
    UnionOp,
    GroupAggOp,
    OrderLimitOp,
]

# -----------------------------
# Plan steps + plan
# -----------------------------

class PlanStep(BaseModel):
    step_id: str
    operation: OpT = Field(discriminator="op")
    output_schema: RelationSchema
    description: Optional[str] = None

class ResultPlan(BaseModel):
    plan_id: str
    steps: List[PlanStep]
    final_output: RelationRef

    @model_validator(mode="after")
    def validate_references(self):
        step_ids = {s.step_id for s in self.steps}

        def check_ref(r: RelationRef):
            if r.source == "step" and r.id not in step_ids:
                raise ValueError(f"Unknown step reference: {r.id}")

        for s in self.steps:
            op = s.operation
            if hasattr(op, "input"):
                check_ref(op.input)
            if hasattr(op, "left"):
                check_ref(op.left)
                check_ref(op.right)
            if hasattr(op, "inputs"):
                for r in op.inputs:
                    check_ref(r)

        check_ref(self.final_output)
        return self
