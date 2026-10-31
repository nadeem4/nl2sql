"""``engine.approvals``: the requests a ``needs_approval`` run stored, and their grants (D215).

Over HTTP the approver and the asker are two callers at different times, so the engine keeps each
``ApprovalRequest`` (pending) from the run that raised it, an approver grants or denies it by id, and the asker's
re-run names the id. A granted id becomes the ``Approval`` the engine already checks (``hooks.approval``), so
Wave A's checks are unchanged. The documents live in the state store (``put_approval`` and friends); a request and
its grant expire ``settings.approvals.max_age_hours`` after their last change, and ``engine.storage.purge()``
deletes them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, Field

from nl2sql.common.errors import ApprovalNotFound, NotAuthorized
from nl2sql.hooks.approval import Approval, ApprovalRequest
from nl2sql.policies import UserContext


class ApprovalRecord(BaseModel):
    """One stored request: ``status`` is ``pending``, ``granted`` or ``denied``; ``id`` is the request's id."""

    id: str
    status: str
    request: ApprovalRequest
    created_at: datetime
    updated_at: datetime
    decided_by: Optional[str] = None
    # Who granted, with the roles they held then: the engine's own record, never shown to callers.
    approver: Optional[UserContext] = Field(default=None, exclude=True)
    reason: Optional[str] = None  # why it was denied


class Approvals:
    """``engine.approvals``. The caller is required on every method (D204)."""

    def __init__(self, ctx: Any):
        self._ctx = ctx

    # -- the run's side ------------------------------------------------------------

    def submit(self, requests: Sequence[ApprovalRequest]) -> None:
        """Store a ``needs_approval`` run's requests as pending. A request already stored is left as it is, unless
        it has expired, which makes it pending again. Nobody calls this but the run."""
        state = self._ctx.state
        now = _now()
        for request in requests:
            existing = state.get_approval(request.id)
            if existing is not None and not self._expired(existing):
                continue
            state.put_approval({"id": request.id, "status": "pending", "created_at": now.isoformat(),
                                "updated_at": now.isoformat(), "request": request.model_dump(mode="json"),
                                "decided_by": None, "approver": None, "reason": None})

    def resolve(self, items: Sequence[Union[Approval, str]], *, user: Optional[UserContext],
                ) -> Tuple[List[Approval], Optional[str]]:
        """``items`` as ``Approval`` objects: an object passes through, a request id becomes the ``Approval`` of its
        grant. The second value says why an id could not (unknown, still waiting, denied or expired), or is None."""
        resolved: List[Approval] = []
        for item in items:
            if isinstance(item, Approval):
                resolved.append(item)
                continue
            doc = self._ctx.state.get_approval(item)
            if doc is None:
                return [], f"There is no approval request '{item}'."
            if self._expired(doc):
                return [], f"The approval request '{item}' has expired."
            if doc["status"] == "pending" or (doc["status"] == "granted" and not doc.get("approver")):
                return [], f"The approval request '{item}' is still waiting for an approver."
            if doc["status"] == "denied":
                return [], f"The approval request '{item}' was denied."
            record = _record(doc)
            resolved.append(Approval(request=record.request, by=record.approver))
        return resolved, None

    # -- the people's side ---------------------------------------------------------

    def list(self, *, user_context: UserContext, status: Optional[str] = "pending") -> List[ApprovalRecord]:
        """The stored requests with ``status`` (all when None), newest first: those the caller's roles may approve,
        and the caller's own."""
        records = [_record(doc) for doc in self._ctx.state.list_approvals(status) if not self._expired(doc)]
        return [r for r in records if self._visible(r, user_context)]

    def grant(self, request_id: str, *, by: UserContext) -> ApprovalRecord:
        """Grant a pending request. ``NotAuthorized`` unless ``by`` holds one of the hook's approver roles;
        ``ApprovalNotFound`` for an unknown or expired id. A request already decided is returned as it is."""
        self._approvable(request_id, by)
        decided = self._decide(request_id, "granted", by)
        if decided["status"] == "granted" and not decided.get("approver"):
            decided = {**decided, "approver": by.model_dump(mode="json")}
            self._ctx.state.put_approval(decided)
        return _record(decided)

    def deny(self, request_id: str, *, by: UserContext, reason: Optional[str] = None) -> ApprovalRecord:
        """Deny a pending request; the same checks as :meth:`grant`."""
        self._approvable(request_id, by)
        decided = self._decide(request_id, "denied", by)
        if decided["status"] == "denied" and decided.get("decided_by") == by.user_id and reason is not None \
                and decided.get("reason") is None:
            decided = {**decided, "reason": reason}
            self._ctx.state.put_approval(decided)
        return _record(decided)

    # -- internals -----------------------------------------------------------------

    def _decide(self, request_id: str, status: str, by: UserContext) -> Dict[str, Any]:
        decided = self._ctx.state.decide_approval(request_id, status, by=by.user_id)
        if decided is None:  # purged between the check and the decision
            raise ApprovalNotFound(f"There is no approval request '{request_id}'.")
        return decided

    def _roles(self, request: ApprovalRequest) -> Sequence[str]:
        return self._ctx.hooks.approver_roles(request.hook)

    def _approvable(self, request_id: str, by: UserContext) -> None:
        doc = self._ctx.state.get_approval(request_id)
        request = ApprovalRequest.model_validate(doc["request"]) if doc else None
        if doc is None or self._expired(doc) or request.asker_tenant_id != by.tenant_id:
            # Another tenant's request is not found, never "not authorized": its existence is not told.
            raise ApprovalNotFound(f"There is no approval request '{request_id}'.")
        roles = self._roles(request)
        if not set(by.roles) & set(roles):
            raise NotAuthorized("Only an approver may decide an approval request.",
                                fix=f"Ask someone with one of these roles: {', '.join(roles) or 'none are configured'}.")

    def _visible(self, record: ApprovalRecord, user: UserContext) -> bool:
        request = record.request
        if request.asker_tenant_id != user.tenant_id:
            return False
        if user.user_id is not None and request.asker_user_id == user.user_id:
            return True
        return bool(set(user.roles) & set(self._roles(request)))

    def _expired(self, doc: Dict[str, Any]) -> bool:
        hours = self._ctx.settings.approvals.max_age_hours
        return _when(doc["updated_at"]) < _now() - timedelta(hours=hours)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(stamp: str) -> datetime:
    moment = datetime.fromisoformat(stamp)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _record(doc: Dict[str, Any]) -> ApprovalRecord:
    return ApprovalRecord.model_validate(doc)
