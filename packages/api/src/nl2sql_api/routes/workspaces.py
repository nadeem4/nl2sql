"""The `workspaces` namespace of `/api/v1` (docs/architecture/wave_d.md, "The routes", D224).

Thin over ``engine.workspaces``: a workspace is a named group of the caller's own dashboards.
"""
from typing import Annotated, List

from fastapi import APIRouter, Depends, Response

from nl2sql import NL2SQL, UserContext, Workspace
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE
from nl2sql_api.models.dashboards import WorkspaceBody

router = APIRouter(tags=["workspaces"])

Caller = Annotated[UserContext, Depends(identity.caller)]
Engine = Annotated[NL2SQL, Depends(get_engine)]

_MISSING = {404: {**ERROR_RESPONSE, "description": "No such workspace (or one that is not the caller's)."},
            **identity.AUTH_RESPONSES}
_NAME = {422: {**ERROR_RESPONSE, "description": "The name is empty or already used."}}


@router.get("/workspaces", response_model=List[Workspace], summary="List workspaces",
            responses=identity.AUTH_RESPONSES)
def list_workspaces(user: Caller, engine: Engine):
    """The caller's workspaces, oldest first."""
    return engine.workspaces.list(user_context=user)


@router.post("/workspaces", response_model=Workspace, status_code=201, summary="Create a workspace",
             responses={**_NAME, **identity.AUTH_RESPONSES})
def create_workspace(body: WorkspaceBody, user: Caller, engine: Engine):
    """Names are unique per owner, ignoring case."""
    return engine.workspaces.create(body.name, user_context=user)


@router.put("/workspaces/{workspace_id}/name", response_model=Workspace, summary="Rename a workspace",
            responses={**_MISSING, **_NAME})
def rename_workspace(workspace_id: str, body: WorkspaceBody, user: Caller, engine: Engine):
    return engine.workspaces.rename(workspace_id, body.name, user_context=user)


@router.delete("/workspaces/{workspace_id}", status_code=204, summary="Delete a workspace",
               responses={**_MISSING, 422: {**ERROR_RESPONSE, "description": "It still holds dashboards."}})
def delete_workspace(workspace_id: str, user: Caller, engine: Engine):
    """An empty workspace only; one that holds dashboards is a 422."""
    engine.workspaces.delete(workspace_id, user_context=user)
    return Response(status_code=204)
