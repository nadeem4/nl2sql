"""The approval shapes: ``ApprovalRequest`` (on ``QueryResult.approvals_needed``) and ``Approval``.

A hook at ``execute.before`` may answer that a person must approve the SQL.
The run then ends ``needs_approval`` with one request per pending sub-query;
an approver re-runs the question with an ``Approval`` that carries it. The
design is docs/architecture/wave_a.md, section 7 (D10, D24).

The engine checks an approval, not the hook: ``approval_problem`` says why
one does not cover the SQL a hook asked about (the runner calls it at
``execute.before``), and ``request_problem`` why one is not this run's at all
(the run calls it before pinning the approved plan). An ``Approval`` object is not stored or signed; the
request's ``id`` depends only on its content, so a store can key on it (``engine.approvals`` keeps requests
and grants by id, and they expire).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Literal, Optional, Sequence

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

    @classmethod
    def build(cls, *, question: str, asker: UserContext, hook: str, datasource_id: str, sql: str, reason: str,
              approver_roles: Sequence[str] = (), decomposition: Optional[DecomposerResponse] = None,
              sub_query: Optional[SubQuery] = None, plan: Optional[PlanModel] = None,
              verified: Optional[VerifiedRef] = None) -> "ApprovalRequest":
        """The request for ``sql``, with its hash and its ``id`` computed."""
        request = cls(id="", question=question, asker_user_id=asker.user_id, asker_tenant_id=asker.tenant_id,
                      hook=hook, datasource_id=datasource_id, decomposition=decomposition, sub_query=sub_query,
                      plan=plan, verified=verified, sql=sql, sql_sha256=sql_sha256(sql), reason=reason,
                      approver_roles=list(approver_roles))
        return request.model_copy(update={"id": request.expected_id()})

    def expected_id(self) -> str:
        """The ``id`` this request's content gives.

        SHA-256, in hex, of the canonical JSON of the fields in
        ``REQUEST_ID_FIELDS`` only, each dumped without the values it holds at
        their defaults. So a field added later to this model, or with a
        default to a nested one (a plan, a sub-query), does not change the id
        of a request made before: a store keyed by id (Wave D) survives an
        upgrade. Changing a default, or the meaning of a field in the list,
        does change ids; do that only on purpose.
        """
        content = {name: self.model_dump(mode="json", include={name}, exclude_defaults=True).get(name)
                   for name in REQUEST_ID_FIELDS}
        return hashlib.sha256(_canonical(content).encode("utf-8")).hexdigest()


# What an ApprovalRequest's id hashes (expected_id). Not reason and approver_roles: they are for display.
REQUEST_ID_FIELDS = ("question", "asker_user_id", "asker_tenant_id", "hook", "point", "datasource_id",
                     "decomposition", "sub_query", "plan", "verified", "sql", "sql_sha256")


class Approval(BaseModel):
    """An approver's yes to one ``ApprovalRequest``, passed back on the re-run. Not signed; it does not expire."""

    request: ApprovalRequest
    by: UserContext


def sql_sha256(sql: str) -> str:
    """The SQL's hash, in hex, as an ``ApprovalRequest`` carries it."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def request_problem(approval: Approval, *, question: str, user: Optional[UserContext]) -> Optional[str]:
    """Why ``approval`` is not one for this run (edited, SQL not its hash, another question or asker), or None.

    The SQL an approver reads must be the SQL whose hash is checked, so a
    relay cannot show harmless SQL beside the hash of other SQL (the id alone
    is an unkeyed hash, which a relay can recompute).
    """
    request = approval.request
    if request.id != request.expected_id():
        return "its request was edited after it was made"
    if request.sql_sha256 != sql_sha256(request.sql):
        return "its SQL does not match its hash"
    if request.question != question:
        return "it is for another question"
    user = user or UserContext()
    if (request.asker_user_id, request.asker_tenant_id) != (user.user_id, user.tenant_id):
        return "it is for another asker"
    return None


def approval_problem(approval: Approval, *, hook: str, roles: Sequence[str], question: str,
                     user: Optional[UserContext], datasource_id: Optional[str], sql: Optional[str],
                     params: Optional[Dict[str, Any]]) -> Optional[str]:
    """Why ``approval`` does not cover what hook ``hook`` asked about at ``execute.before``, or None.

    It covers it when the request is this run's (``request_problem``), is for
    this hook and datasource, its SQL's hash is the hash of ``sql`` just
    generated, its verified values (if any) are ``params``, and ``by`` holds
    one of ``roles``: the hook's configured approver roles, never the roles
    the request lists.
    """
    problem = request_problem(approval, question=question, user=user)
    if problem:
        return problem
    request = approval.request
    if request.hook != hook:
        return f"it is for the hook '{request.hook}'"
    if request.datasource_id != datasource_id:
        return "it is for another datasource"
    if request.sql_sha256 != sql_sha256(sql or ""):
        return "it is for other SQL"
    approved_values = request.verified.params if request.verified is not None else {}
    if _canonical(approved_values) != _canonical(params or {}):
        return "it is for other values"
    if not set(approval.by.roles) & set(roles):
        return f"its approver has none of the roles that may approve: {', '.join(sorted(roles)) or 'none'}"
    return None
