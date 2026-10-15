from __future__ import annotations
from typing import List, Optional, Literal, Dict, Any
import re

from pydantic import BaseModel, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from nl2sql.aggregation.columns import input_order, resolve_key
from nl2sql.aggregation.join_keys import DTYPE_CATEGORY, coercible

# The decomposer emits semantic intent only; the model never writes SQL. Words
# such as "from", "where" or "select" are ordinary English ("customers from
# Brazil"), so only SQL syntax is rejected: a statement that opens with
# SELECT ... FROM, a statement terminator, or a comment marker.
_SQL_SYNTAX = re.compile(
    r"^\W*select\b.*\bfrom\b|;|--|/\*|\*/",
    re.IGNORECASE | re.DOTALL,
)


def _contains_sql(*texts: str) -> bool:
    """True if any single text looks like SQL. Each field is checked alone."""
    return any(_SQL_SYNTAX.search(text) for text in texts)


class MetricSpec(BaseModel):
    name: str
    aggregation: Optional[Literal["count", "sum", "avg", "min", "max"]] = None
    description: Optional[str] = None


class FilterSpec(BaseModel):
    attribute: str
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "between", "in", "contains"]
    value: str | int | float | bool | List[str] | List[int] | List[float]


class GroupBySpec(BaseModel):
    attribute: str


class OrderBySpec(BaseModel):
    attribute: str
    direction: Literal["asc", "desc"] = "asc"


Dtype = Literal["string", "int", "float", "bool", "date", "datetime"]


class ExpectedColumn(BaseModel):
    name: str
    dtype: Optional[Dtype] = None
    # Set by the decomposer node on its own copy of each sub-query, never by
    # the model: kept out of the tool schema. The logical validator checks the
    # type of a join-key column against `dtype` (wave_a.md, D30).
    join_key: SkipJsonSchema[bool] = False


class SubQuery(BaseModel):
    id: str
    datasource_id: str
    intent: str
    kind: Literal["scan"] = "scan"
    metrics: List[MetricSpec] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)
    group_by: List[GroupBySpec] = Field(default_factory=list)
    # Ranking and top-N of this sub-query's own rows ("the artist with the
    # most albums" is order_by album_count desc, limit 1).
    order_by: List[OrderBySpec] = Field(default_factory=list)
    limit: Optional[int] = None
    expected_schema: List[ExpectedColumn] = Field(default_factory=list)
    schema_version: Optional[str] = None

    @model_validator(mode="after")
    def validate_semantic_only(self):
        if _contains_sql(
            self.intent,
            *(m.name for m in self.metrics),
            *(f.attribute for f in self.filters),
            *(g.attribute for g in self.group_by),
            *(o.attribute for o in self.order_by),
            *(c.name for c in self.expected_schema),
        ):
            raise ValueError("SubQuery contains SQL syntax.")
        return self


class CombineInput(BaseModel):
    subquery_id: str
    role: Optional[Literal["left", "right", "base", "compare", "primary", "secondary"]] = None


class JoinKeyPair(BaseModel):
    left: str
    right: str
    # Each side's declared dtype, copied from its sub-query's expected_schema
    # by the decomposer node, never by the model (kept out of the tool
    # schema). The aggregator casts both sides to one type with them.
    left_dtype: SkipJsonSchema[Optional[Dtype]] = None
    right_dtype: SkipJsonSchema[Optional[Dtype]] = None


class CombineGroup(BaseModel):
    group_id: str
    operation: Literal["standalone", "compare", "join", "union"]
    inputs: List[CombineInput]
    join_keys: List[JoinKeyPair] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_roles(self):
        if self.operation in {"compare", "join"}:
            if any(i.role is None for i in self.inputs):
                raise ValueError("Compare/join combine groups require roles.")
            if not self.join_keys:
                raise ValueError("Compare/join combine groups require join_keys.")
        return self


class PostCombineOp(BaseModel):
    op_id: str
    target_group_id: str
    operation: Literal["filter", "aggregate", "project", "sort", "limit"]
    filters: List[FilterSpec] = Field(default_factory=list)
    metrics: List[MetricSpec] = Field(default_factory=list)
    group_by: List[GroupBySpec] = Field(default_factory=list)
    order_by: List[OrderBySpec] = Field(default_factory=list)
    limit: Optional[int] = None
    expected_schema: List[ExpectedColumn] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_semantic_only(self):
        if _contains_sql(
            *(m.name for m in self.metrics),
            *(f.attribute for f in self.filters),
            *(g.attribute for g in self.group_by),
            *(o.attribute for o in self.order_by),
            *(c.name for c in self.expected_schema),
        ):
            raise ValueError("PostCombineOp contains SQL syntax.")
        return self


class UnmappedSubQuery(BaseModel):
    intent: str
    reason: Literal["no_datasource", "restricted_datasource", "unsupported_datasource"]
    datasource_id: Optional[str] = None
    detail: Optional[str] = None


class DecomposerResponse(BaseModel):
    """The question decomposed into semantic sub-queries and how to combine them.

    Call this tool with the decomposition itself as the arguments: sub_queries,
    combine_groups, post_combine_ops and unmapped_subqueries at the top level.
    Do not wrap them in another object or under any other key.
    """
    # This docstring is the tool's description on the wire. Without it the tool
    # went out with an empty description and Claude nested the arguments under
    # a key of its own ("input", "query", "dtype") on almost half the questions.

    sub_queries: List[SubQuery] = Field(
        description="One semantic sub-query per datasource-level intent of the question."
    )
    combine_groups: List[CombineGroup] = Field(
        description="How the sub-queries' results combine; a single sub-query is one standalone group."
    )
    post_combine_ops: List[PostCombineOp] = Field(
        default_factory=list,
        description="Operations on a combined group's result. Empty when one sub-query answers the question.",
    )
    unmapped_subqueries: List[UnmappedSubQuery] = Field(
        default_factory=list,
        description="Intents no resolved datasource can answer.",
    )

    @model_validator(mode="after")
    def validate_references(self):
        ids = {sq.id for sq in self.sub_queries}
        for group in self.combine_groups:
            for inp in group.inputs:
                if inp.subquery_id not in ids:
                    raise ValueError(f"CombineGroup references unknown subquery: {inp.subquery_id}")
        group_ids = {g.group_id for g in self.combine_groups}
        for op in self.post_combine_ops:
            if op.target_group_id not in group_ids:
                raise ValueError(f"PostCombineOp references unknown combine group: {op.target_group_id}")
        return self

    @model_validator(mode="after")
    def validate_join_key_types(self):
        """Each join key names a typed column of its side, and the two types can meet (D30).

        The aggregator casts both sides of a key to one type before the join,
        so each side must declare one, in its sub-query's ``expected_schema``,
        and the two must be coercible: a number or a boolean is never a date.
        A violation is a ``ValueError``, which parse-and-retry sends back to
        the model. A group with one input left (the node refuses it) is not
        checked.
        """
        sub_queries = {sq.id: sq for sq in self.sub_queries}
        for group in self.combine_groups:
            if group.operation not in {"join", "compare"} or len(group.inputs) < 2:
                continue
            left, right = (sub_queries[i.subquery_id] for i in
                           sorted(group.inputs, key=lambda i: input_order(i.role, i.subquery_id))[:2])
            for pair in group.join_keys:
                left_dtype = _key_dtype(group.group_id, "left", pair.left, left)
                right_dtype = _key_dtype(group.group_id, "right", pair.right, right)
                if not coercible(DTYPE_CATEGORY[left_dtype], DTYPE_CATEGORY[right_dtype]):
                    raise ValueError(
                        f"Combine group '{group.group_id}': join key '{pair.left}' is declared {left_dtype} and "
                        f"'{pair.right}' {right_dtype}; a {left_dtype} and a {right_dtype} cannot be joined. "
                        f"Join on columns that hold the same kind of value."
                    )
        return self


def key_column(key: str, sub_query: SubQuery) -> Optional[ExpectedColumn]:
    """The ``expected_schema`` column a join key names, side-qualified or not."""
    names = [c.name for c in sub_query.expected_schema]
    name = resolve_key(names, key)
    return next((c for c in sub_query.expected_schema if c.name == name), None)


def _key_dtype(group_id: str, side: str, key: str, sub_query: SubQuery) -> str:
    column = key_column(key, sub_query)
    if column is None:
        raise ValueError(
            f"Combine group '{group_id}': the {side} join key '{key}' is not in the expected_schema of "
            f"sub-query '{sub_query.id}'. Each join key must be one of its side's expected_schema names."
        )
    if column.dtype is None:
        raise ValueError(
            f"Combine group '{group_id}': the {side} join key '{key}' has no dtype in the expected_schema of "
            f"sub-query '{sub_query.id}'. Declare the dtype of every join key column."
        )
    return column.dtype
