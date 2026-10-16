"""The explanation's shapes: ``QueryResult.explanation`` and each clarification option's (D51)."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class TermUse(BaseModel):
    """A glossary term sent with a step whose columns the plan reads."""

    term: str
    definition: str
    source: str  # the knowledge source it came from


class ExplainedStep(BaseModel):
    """One sub-query in plain English, rendered from its typed plan; the same plan always gives the same words."""

    sub_query_id: Optional[str] = None
    datasource_id: str
    source: Literal["generated", "cache", "supplied", "approved", "chosen", "verified"]
    tables: List[str] = Field(default_factory=list)  # "Invoice: one row per customer invoice (glossary)"
    joins: List[str] = Field(default_factory=list)  # "each InvoiceLine to its Invoice, on InvoiceId (declared key)"
    filters: List[str] = Field(default_factory=list)  # "Invoice date is in 2012"
    groupings: List[str] = Field(default_factory=list)  # "by Customer country"
    measures: List[str] = Field(default_factory=list)  # "revenue: total of InvoiceLine unit price times quantity"
    order: Optional[str] = None  # "highest revenue first, top 10"
    terms: List[TermUse] = Field(default_factory=list)
    rules: List[str] = Field(default_factory=list)  # the rules sent with this step, by text
    assumptions: List[str] = Field(default_factory=list)
    planner_note: Optional[str] = None  # the plan's own reasoning, labelled as the planner's (model text)
    text: str  # the step as two or three sentences


class Explanation(BaseModel):
    """The run's plan in plain English: one step per sub-query, and how the steps were combined."""

    steps: List[ExplainedStep] = Field(default_factory=list)
    combined: Optional[str] = None
