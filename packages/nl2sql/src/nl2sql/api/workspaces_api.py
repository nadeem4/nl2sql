"""``engine.workspaces``: the folders dashboards live in (docs/architecture/wave_c.md, section 6).

The logic is ``nl2sql.dashboards.workspaces``. Private to the owner (D118): the caller is required (D130).
"""
from __future__ import annotations

from typing import List

from nl2sql.dashboards.models import Workspace
from nl2sql.dashboards.store import DashboardStore
from nl2sql.dashboards.workspaces import Workspaces
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime


class WorkspacesAPI:
    """``engine.workspaces``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx
        self._service = Workspaces(ctx, DashboardStore(ctx.state))

    def create(self, name: str, *, user_context: UserContext) -> Workspace:
        """A new workspace owned by the caller. Names are unique per owner, ignoring case."""
        return self._service.create(name, user_context=user_context)

    def rename(self, workspace_id: str, name: str, *, user_context: UserContext) -> Workspace:
        """Renames one of the caller's workspaces.

        Raises:
            WorkspaceNotFound: it does not exist or is not the caller's.
        """
        return self._service.rename(workspace_id, name, user_context=user_context)

    def list(self, *, user_context: UserContext) -> List[Workspace]:
        """The caller's workspaces, oldest first."""
        return self._service.list(user_context=user_context)

    def delete(self, workspace_id: str, *, user_context: UserContext) -> None:
        """Deletes an empty workspace.

        Raises:
            WorkspaceNotEmpty: it still holds dashboards.
        """
        self._service.delete(workspace_id, user_context=user_context)
