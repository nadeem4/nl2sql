"""The dashboards service behind ``engine.dashboards`` (docs/architecture/wave_c.md, section 6).

D1 is the store and the manual edits: a dashboard is private to its owner (D118), lives in one of their
workspaces (D131), is built as a draft and saved whole by ``pin``. Nothing here runs a chart or calls a model.
Refresh arrives with D2 and the starter dashboard with D3.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional, Sequence

from nl2sql.conversations.store import Owner, owner_of
from nl2sql.dashboards.edits import Applied, EditEnv, apply_edits
from nl2sql.dashboards.models import (
    Arrange, ChartChoice, Dashboard, DashboardEdit, DashboardFilter, DashboardInfo, MoveDashboard, RemoveTile,
    RenameDashboard, RenameTile, ScheduleIdentity, SetFilters, SetSchedule, SetTileChart, TileArrangement,
)
from nl2sql.dashboards.store import DashboardStore
from nl2sql.dashboards.workspaces import Workspaces
from nl2sql.events.catalog import DashboardChanged, DashboardScheduleSet
from nl2sql.policies import UserContext

_STATUSES = ("draft", "saved")


class Dashboards:
    """What ``engine.dashboards`` does. ``DashboardsAPI`` is its public face."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._store = DashboardStore(ctx.state)
        self._workspaces = Workspaces(ctx, self._store)

    def _owner(self, user: UserContext) -> Owner:
        return owner_of(user, self._ctx.tenant_id)

    # ------------------------------------------------------------ the store

    def get(self, dashboard_id: str, *, user_context: UserContext) -> Dashboard:
        return self._store.get(dashboard_id, self._owner(user_context))

    def list(self, *, user_context: UserContext, workspace_id: Optional[str] = None,
             status: str = "saved") -> List[DashboardInfo]:
        if status not in _STATUSES:
            raise ValueError(f"status must be one of {', '.join(_STATUSES)}, not '{status}'.")
        infos = []
        for d in self._store.list(self._owner(user_context)):
            if d.status != status or (workspace_id is not None and d.workspace_id != workspace_id):
                continue
            tiles = {t.id for t in d.tiles}
            ats = [run.at for run in self._store.runs(d.id) if run.tile_id in tiles]
            infos.append(DashboardInfo(id=d.id, title=d.title, workspace_id=d.workspace_id, status=d.status,
                                       tiles=len(d.tiles), refreshed_at=min(ats) if ats else None,
                                       updated_at=d.updated_at))
        return infos

    def delete(self, dashboard_id: str, *, user_context: UserContext) -> None:
        self.get(dashboard_id, user_context=user_context)
        self._store.delete(dashboard_id)
        self._changed(dashboard_id, ["deleted"], user_context)

    def pin(self, dashboard_id: str, *, user_context: UserContext, workspace_id: Optional[str] = None,
            title: Optional[str] = None) -> Dashboard:
        owner = self._owner(user_context)
        dashboard = self._store.get(dashboard_id, owner)
        if dashboard.status == "saved":
            raise ValueError(f"Dashboard '{dashboard_id}' is already saved.")
        if not dashboard.tiles:
            raise ValueError("An empty draft cannot be pinned: add a chart first.")
        if workspace_id is not None:
            target = self._store.workspace(workspace_id, owner)
        else:
            mine = {w.id: w for w in self._store.workspaces(owner)}
            target = mine.get(dashboard.workspace_id) or self._workspaces.default(owner, user_context)
        changes = {"status": "saved", "workspace_id": target.id, "updated_at": datetime.now(timezone.utc)}
        if title is not None:
            changes["title"] = title.strip() or dashboard.title
        saved = dashboard.model_copy(update=changes)
        self._store.put(saved)
        self._changed(dashboard_id, ["pinned"], user_context)
        return saved

    # ------------------------------------------------------------ the edits

    def edit(self, dashboard_id: str, edits: Sequence[DashboardEdit], *, user_context: UserContext) -> Dashboard:
        owner = self._owner(user_context)
        dashboard = self._store.get(dashboard_id, owner)
        if not edits:
            return dashboard
        now = datetime.now(timezone.utc)
        env = EditEnv(
            identity=ScheduleIdentity(user_id=user_context.user_id, tenant_id=user_context.tenant_id,
                                      roles=list(user_context.roles)),
            now=now, min_refresh_minutes=self._ctx.config.dashboards.min_refresh_minutes,
            workspace_ids={w.id for w in self._store.workspaces(owner)}, chart_settings=self._ctx.settings.charts,
            last_chart=lambda tile_id: self._store.last_chart(dashboard_id, tile_id))
        applied = apply_edits(dashboard, edits, env)  # raises before anything is stored: all or none
        saved = applied.dashboard.model_copy(update={"updated_at": now})
        self._store.put(saved)
        self._store.drop_runs(dashboard_id, applied.dropped)
        self._published(applied, saved, user_context)
        return saved

    def rename(self, dashboard_id: str, title: str, *, user_context: UserContext) -> Dashboard:
        return self.edit(dashboard_id, [RenameDashboard(title=title)], user_context=user_context)

    def rename_tile(self, dashboard_id: str, tile_id: str, title: str, *, user_context: UserContext) -> Dashboard:
        return self.edit(dashboard_id, [RenameTile(tile_id=tile_id, title=title)], user_context=user_context)

    def set_tile_chart(self, dashboard_id: str, tile_id: str, chart: ChartChoice, *,
                       user_context: UserContext) -> Dashboard:
        return self.edit(dashboard_id, [SetTileChart(tile_id=tile_id, chart=chart)], user_context=user_context)

    def remove_tile(self, dashboard_id: str, tile_id: str, *, user_context: UserContext) -> Dashboard:
        return self.edit(dashboard_id, [RemoveTile(tile_id=tile_id)], user_context=user_context)

    def arrange(self, dashboard_id: str, layouts: List[TileArrangement], *, user_context: UserContext) -> Dashboard:
        return self.edit(dashboard_id, [Arrange(layouts=layouts)], user_context=user_context)

    def set_filters(self, dashboard_id: str, filters: List[DashboardFilter], *,
                    user_context: UserContext) -> Dashboard:
        return self.edit(dashboard_id, [SetFilters(filters=filters)], user_context=user_context)

    def set_schedule(self, dashboard_id: str, every_minutes: Optional[int], *,
                     user_context: UserContext) -> Dashboard:
        return self.edit(dashboard_id, [SetSchedule(every_minutes=every_minutes)], user_context=user_context)

    def move(self, dashboard_id: str, workspace_id: str, *, user_context: UserContext) -> Dashboard:
        return self.edit(dashboard_id, [MoveDashboard(workspace_id=workspace_id)], user_context=user_context)

    # ------------------------------------------------------------ events

    def _changed(self, dashboard_id: str, change: List[str], user: UserContext) -> None:
        self._ctx.bus.publish("dashboard.changed", DashboardChanged(
            dashboard_id=dashboard_id, change=change, by=user.user_id), tenant_id=self._owner(user)[0])

    def _published(self, applied: Applied, saved: Dashboard, user: UserContext) -> None:
        self._changed(saved.id, applied.kinds, user)
        if applied.schedule_set:
            every = saved.schedule.every_minutes if saved.schedule else None
            self._ctx.bus.publish("dashboard.schedule_set", DashboardScheduleSet(
                dashboard_id=saved.id, every_minutes=every, by=user.user_id), tenant_id=self._owner(user)[0])
