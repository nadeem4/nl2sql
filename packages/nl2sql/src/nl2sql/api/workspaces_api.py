"""``engine.workspaces``: the folders dashboards live in (docs/architecture/wave_c.md, section 6).

A stub with its final signatures; D1 fills it. Private to the owner (D118): the caller is required (D130).
"""
from __future__ import annotations

from typing import List

from nl2sql.common.planned import arrives_with
from nl2sql.dashboards.models import Workspace
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime


class WorkspacesAPI:
    """``engine.workspaces``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def create(self, name: str, *, user_context: UserContext) -> Workspace:
        """A new workspace owned by the caller. Names are unique per owner."""
        raise arrives_with("engine.workspaces.create", "D1")

    def rename(self, workspace_id: str, name: str, *, user_context: UserContext) -> Workspace:
        """Renames one of the caller's workspaces.

        Raises:
            WorkspaceNotFound: it does not exist or is not the caller's.
        """
        raise arrives_with("engine.workspaces.rename", "D1")

    def list(self, *, user_context: UserContext) -> List[Workspace]:
        """The caller's workspaces."""
        raise arrives_with("engine.workspaces.list", "D1")

    def delete(self, workspace_id: str, *, user_context: UserContext) -> None:
        """Deletes an empty workspace.

        Raises:
            WorkspaceNotEmpty: it still holds dashboards.
        """
        raise arrives_with("engine.workspaces.delete", "D1")
