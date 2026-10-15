"""A clarification: the options a run offers instead of guessing, and the caller's pick (D47, D49).

Nothing is stored: the caller holds the request and passes it back in a
``ClarificationChoice``. ``id`` is an unkeyed hash of the request's content,
a consistency check against mistakes and mixed-up requests, not tamper
evidence: the validator, run again on the chosen plan with the caller's
scope, is the guard (as for Wave A's ``ApprovalRequest``, D24).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from nl2sql.explain.models import ExplainedStep


class ClarificationOption(BaseModel):
    """One reading of the question: ``a`` is the default, each with a validated plan."""

    id: str  # "a", "b", "c"
    label: str
    plan: Dict[str, Any]  # the PlanModel as JSON
    explanation: ExplainedStep


class ClarificationRequest(BaseModel):
    """What ``QueryResult.clarification`` carries when the run ends ``needs_clarification``."""

    id: str  # SHA-256 of every other field, hex, 32 characters
    question: str  # as passed to run_query, before run.before
    asker_user_id: Optional[str] = None
    asker_tenant_id: Optional[str] = None
    phrase: str
    datasource_id: str
    schema_version: str
    knowledge_digest: str
    sub_query: Dict[str, Any]  # the SubQuery as JSON
    options: List[ClarificationOption] = Field(min_length=2, max_length=3)

    @classmethod
    def build(cls, **fields: Any) -> "ClarificationRequest":
        """A request with its ``id`` computed from the other fields."""
        draft = cls(id="", **fields)
        return draft.model_copy(update={"id": draft.expected_id()})

    def expected_id(self) -> str:
        """The id this request's content hashes to: SHA-256 of its canonical JSON without ``id``, 32 hex."""
        content = self.model_dump(mode="json", exclude={"id"})
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class ClarificationChoice(BaseModel):
    """The caller's pick, passed to ``run_query(choice=...)`` (from L2) to run that option's plan."""

    request: ClarificationRequest
    option_id: str
