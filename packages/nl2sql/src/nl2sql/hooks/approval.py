"""The approval shapes: ``ApprovalRequest`` (on ``QueryResult.approvals_needed``) and ``Approval``.

A hook at ``execute.before`` may answer that a person must approve the SQL.
The run then ends ``needs_approval`` with one request per pending sub-query;
an approver re-runs the question with an ``Approval`` that carries it. The
design is docs/architecture/wave_a.md, section 7 (D10, D24). The shapes are
here from the foundation PR so the result carries them; computing the
request's ``id`` and checking an approval arrive with H2.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery
from nl2sql_sdk.policy import UserContext


class VerifiedRef(BaseModel):
    """A verified query from ``curated.yaml``, by id, with the values it was bound to."""

    id: str
    params: Dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    """What a person is asked to approve: one sub-query's SQL, bound to the asker.

    The generated path pins the run's ``decomposition``, the ``sub_query`` the
    SQL answers and its validated ``plan``; the verified path pins the
    ``verified`` query and its values. ``id`` is a SHA-256, in hex, over every
    field but ``reason`` and ``approver_roles``, so the engine can tell an
    edited request. ``approver_roles`` is for display: the engine checks the
    hook's configured roles.
    """

    id: str
    question: str
    asker_user_id: Optional[str] = None
    asker_tenant_id: Optional[str] = None
    hook: str
    point: Literal["execute.before"] = "execute.before"
    datasource_id: str
    decomposition: Optional[DecomposerResponse] = None
    sub_query: Optional[SubQuery] = None
    plan: Optional[PlanModel] = None
    verified: Optional[VerifiedRef] = None
    sql: str
    sql_sha256: str
    reason: str
    approver_roles: List[str] = Field(default_factory=list)


class Approval(BaseModel):
    """An approver's yes to one ``ApprovalRequest``, passed back on the re-run. Not signed; it does not expire."""

    request: ApprovalRequest
    by: UserContext
