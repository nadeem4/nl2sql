"""The `approvals` namespace of `/api/v1`.

A run a hook held for approval stores a request; an approver (a role the hook names) grants or denies it; the asker then
re-asks with `approval_ids`. Who may decide is the engine's rule: a caller without the role gets its 403, another
tenant's request is a 404.
"""
from typing import Annotated, List, Literal, Optional

from fastapi import APIRouter, Body, Depends, Query

from nl2sql import NL2SQL, ApprovalRecord, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE
from nl2sql_api.models.query import DenyRequest

router = APIRouter(tags=["approvals"])

Caller = Annotated[UserContext, Depends(identity.caller)]
Engine = Annotated[NL2SQL, Depends(get_engine)]
_DECIDE = {403: {**ERROR_RESPONSE, "description": "The caller holds none of the roles that may approve this."},
           404: {**ERROR_RESPONSE, "description": "No such approval request for this caller."}, **identity.AUTH_RESPONSES}


@router.get("/approvals", response_model=List[ApprovalRecord], summary="List approval requests",
            responses=identity.AUTH_RESPONSES)
def list_approvals(engine: Engine, user_context: Caller,
                   status: Annotated[Literal["pending", "granted", "denied"], Query(
                       description="Only requests in this state.")] = "pending"):
    """The requests the caller asked, plus those their roles may decide, in `status` (default `pending`). Who granted
    one is the engine's record and is not returned."""
    return engine.approvals.list(user_context=user_context, status=status)


@router.post("/approvals/{request_id}/grant", response_model=ApprovalRecord, summary="Grant an approval",
             responses=_DECIDE)
def grant_approval(request_id: str, engine: Engine, user_context: Caller):
    """Grant the request. The asker then re-asks the question with `approval_ids: [request_id]`."""
    return engine.approvals.grant(request_id, by=user_context)


@router.post("/approvals/{request_id}/deny", response_model=ApprovalRecord, summary="Deny an approval",
             responses=_DECIDE)
def deny_approval(request_id: str, engine: Engine, user_context: Caller,
                  payload: Annotated[Optional[DenyRequest], Body()] = None):
    """Deny the request, with an optional `reason`. A run that names it ends with `APPROVAL_NOT_GRANTED`."""
    return engine.approvals.deny(request_id, by=user_context, reason=payload.reason if payload else None)
