"""Request bodies of the query, feedback and approval routes (docs/architecture/wave_d.md, D201-D204).

Every body forbids unknown fields, so a misspelt or removed field is a 422 rather than silently ignored. Responses are
the facade's own models (``QueryResult``, ``ResultPage``, ``Proposal``, ``ApprovalRecord``), never a copy of them.
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2sql import ClarificationChoice


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryRequest(_Body):
    question: str
    datasource_id: Optional[str] = None
    execute: bool = True
    choice: Optional[ClarificationChoice] = Field(
        default=None, description="The option picked from an earlier `clarification_request`; asks the same question again.")
    approval_ids: List[str] = Field(
        default_factory=list, description="Ids of approvals an approver granted, from an earlier `needs_approval` run.")


class FeedbackRequest(_Body):
    trace_id: str
    rating: Literal["up", "down"]
    note: Optional[str] = None


class CorrectionRequest(_Body):
    trace_id: str
    sql: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None
    question: Optional[str] = None
    note: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_fix(self):
        if (self.sql is None) == (self.plan is None):
            raise ValueError("send exactly one of sql or plan")
        return self


class DenyRequest(_Body):
    reason: Optional[str] = None
