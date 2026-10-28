"""The workspaces service behind ``engine.workspaces`` (docs/architecture/wave_c.md, section 6; D118, D131).

A workspace is a named, private group of its owner's dashboards. The caller is required on every method (D130).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, List

from nl2sql.common.errors import WorkspaceNotEmpty
from nl2sql.conversations.store import Owner, owner_of
from nl2sql.dashboards.models import DEFAULT_WORKSPACE_NAME, Workspace
from nl2sql.dashboards.store import DashboardStore
from nl2sql.events.catalog import WorkspaceChanged
from nl2sql.policies import UserContext


class Workspaces:
    """What ``engine.workspaces`` does. ``WorkspacesAPI`` is its public face."""

    def __init__(self, ctx: Any, store: DashboardStore) -> None:
        self._ctx = ctx
        self._store = store

    def create(self, name: str, *, user_context: UserContext) -> Workspace:
        owner = owner_of(user_context, self._ctx.tenant_id)
        name = self._unique(name, owner)
        workspace = Workspace(id=secrets.token_urlsafe(12), name=name, tenant_id=owner[0], user_id=owner[1],
                              created_at=datetime.now(timezone.utc))
        self._store.put_workspace(workspace)
        self._changed(workspace, "created", user_context)
        return workspace

    def rename(self, workspace_id: str, name: str, *, user_context: UserContext) -> Workspace:
        owner = owner_of(user_context, self._ctx.tenant_id)
        workspace = self._store.workspace(workspace_id, owner)
        renamed = workspace.model_copy(update={"name": self._unique(name, owner, keep=workspace_id)})
        self._store.put_workspace(renamed)
        self._changed(renamed, "renamed", user_context)
        return renamed

    def list(self, *, user_context: UserContext) -> List[Workspace]:
        return self._store.workspaces(owner_of(user_context, self._ctx.tenant_id))

    def delete(self, workspace_id: str, *, user_context: UserContext) -> None:
        owner = owner_of(user_context, self._ctx.tenant_id)
        workspace = self._store.workspace(workspace_id, owner)
        if any(d.workspace_id == workspace_id for d in self._store.list(owner)):
            raise WorkspaceNotEmpty(f"Workspace '{workspace.name}' still holds dashboards; move or delete them first.")
        self._store.delete_workspace(workspace_id)
        self._changed(workspace, "deleted", user_context)

    def default(self, owner: Owner, user_context: UserContext) -> Workspace:
        """The owner's "General" workspace, created on first use."""
        for workspace in self._store.workspaces(owner):
            if workspace.name.casefold() == DEFAULT_WORKSPACE_NAME.casefold():
                return workspace
        return self.create(DEFAULT_WORKSPACE_NAME, user_context=user_context)

    def _unique(self, name: str, owner: Owner, keep: str = "") -> str:
        name = name.strip()
        if not name:
            raise ValueError("A workspace name cannot be empty.")
        if any(w.name.casefold() == name.casefold() and w.id != keep for w in self._store.workspaces(owner)):
            raise ValueError(f"This owner already has a workspace named '{name}'.")
        return name

    def _changed(self, workspace: Workspace, change: str, user: UserContext) -> None:
        self._ctx.bus.publish("workspace.changed", WorkspaceChanged(
            workspace_id=workspace.id, change=change, by=user.user_id), tenant_id=workspace.tenant_id)
