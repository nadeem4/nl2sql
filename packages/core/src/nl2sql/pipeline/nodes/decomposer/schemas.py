from __future__ import annotations
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, model_validator


def _contains_physical_tokens(text: str) -> bool:
    forbidden = ["select", "from", "join", "where", "group by", "order by", ";", "--", "/*", "*/"]
    lowered = text.lower()
    return any(token in lowered for token in forbidden)


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


class ExpectedColumn(BaseModel):
    name: str
    dtype: Optional[Literal["string", "int", "float", "bool", "date", "datetime"]] = None


class SubQuery(BaseModel):
    id: str
    datasource_id: str
    intent: str
    kind: Literal["scan"] = "scan"
    metrics: List[MetricSpec] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)
    group_by: List[GroupBySpec] = Field(default_factory=list)
    expected_schema: List[ExpectedColumn] = Field(default_factory=list)
    schema_version: Optional[str] = None

    @model_validator(mode="after")
    def validate_semantic_only(self):
        content = " ".join(
            [
                self.intent,
                " ".join(m.name for m in self.metrics),
                " ".join(f.attribute for f in self.filters),
                " ".join(g.attribute for g in self.group_by),
                " ".join(c.name for c in self.expected_schema),
            ]
        ).lower()
        if _contains_physical_tokens(content):
            raise ValueError("SubQuery contains SQL or physical schema tokens.")
        return self


class CombineInput(BaseModel):
    subquery_id: str
    role: Optional[Literal["left", "right", "base", "compare", "primary", "secondary"]] = None


class JoinKeyPair(BaseModel):
    left: str
    right: str


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
        content = " ".join(
            [
                " ".join(m.name for m in self.metrics),
                " ".join(f.attribute for f in self.filters),
                " ".join(g.attribute for g in self.group_by),
                " ".join(o.attribute for o in self.order_by),
                " ".join(c.name for c in self.expected_schema),
            ]
        )
        if _contains_physical_tokens(content):
            raise ValueError("PostCombineOp contains SQL or physical schema tokens.")
        return self


class UnmappedSubQuery(BaseModel):
    intent: str
    reason: Literal["no_datasource", "restricted_datasource", "unsupported_datasource"]
    datasource_id: Optional[str] = None
    detail: Optional[str] = None


class DecomposerResponse(BaseModel):
    sub_queries: List[SubQuery]
    combine_groups: List[CombineGroup]
    post_combine_ops: List[PostCombineOp] = Field(default_factory=list)
    unmapped_subqueries: List[UnmappedSubQuery] = Field(default_factory=list)

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
