"""Workspaces and dashboards in the state store: ownership, and the models behind the JSON documents (D118, D131).

A workspace or a dashboard is its owner's, a ``(tenant_id, user_id)`` pair as for a conversation: another
caller's is "not found", never "forbidden".
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional

from nl2sql.charts.spec import ChartSpec
from nl2sql.common.errors import DashboardNotFound, WorkspaceNotFound
from nl2sql.conversations.store import Owner
from nl2sql.dashboards.models import Dashboard, TileRun, Workspace


class DashboardStore:
    """The state store's workspace, dashboard and stored-result documents as models."""

    def __init__(self, state: Any) -> None:
        self._state = state

    # ---- workspaces

    def workspace(self, workspace_id: str, owner: Owner) -> Workspace:
        doc = self._state.get_workspace(workspace_id)
        if doc is None or (doc.get("tenant_id"), doc.get("user_id")) != owner:
            raise WorkspaceNotFound(f"No workspace '{workspace_id}' for this caller.")
        return Workspace.model_validate(doc)

    def workspaces(self, owner: Owner) -> List[Workspace]:
        return [Workspace.model_validate(doc) for doc in self._state.list_workspaces(*owner)]

    def put_workspace(self, workspace: Workspace) -> None:
        self._state.put_workspace(workspace.model_dump(mode="json"))

    def delete_workspace(self, workspace_id: str) -> None:
        self._state.delete_workspace(workspace_id)

    # ---- dashboards

    def get(self, dashboard_id: str, owner: Owner) -> Dashboard:
        doc = self._state.get_dashboard(dashboard_id)
        if doc is None or (doc.get("tenant_id"), doc.get("user_id")) != owner:
            raise DashboardNotFound(f"No dashboard '{dashboard_id}' for this caller.")
        return Dashboard.model_validate(doc)

    def list(self, owner: Owner) -> List[Dashboard]:
        """The owner's dashboards of every status, newest first."""
        return [Dashboard.model_validate(doc) for doc in self._state.list_dashboards(*owner)]

    def put(self, dashboard: Dashboard) -> None:
        self._state.put_dashboard(dashboard.model_dump(mode="json"))

    def delete(self, dashboard_id: str) -> None:
        self._state.delete_dashboard(dashboard_id)

    # ---- stored results

    def runs(self, dashboard_id: str) -> List[TileRun]:
        return [TileRun.model_validate(doc) for doc in self._state.get_tile_runs(dashboard_id)]

    def drop_runs(self, dashboard_id: str, tile_ids: Iterable[str]) -> None:
        tile_ids = list(tile_ids)
        if tile_ids:
            self._state.delete_tile_runs(dashboard_id, tile_ids)

    def last_chart(self, dashboard_id: str, tile_id: str) -> Optional[ChartSpec]:
        """The chart of a tile's last stored result, or None when it has none."""
        for run in self.runs(dashboard_id):
            if run.tile_id == tile_id:
                return run.chart
        return None
