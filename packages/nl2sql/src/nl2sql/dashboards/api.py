"""The dashboards service behind ``engine.dashboards`` (docs/architecture/wave_c.md, section 6).

D1 is the store and the manual edits: a dashboard is private to its owner (D118), lives in one of their
workspaces (D131), is built as a draft and saved whole by ``pin``. D2 runs the charts: ``open`` serves the stored
results after the access check, ``refresh`` re-runs the stored plans as the caller, ``run_scheduled`` does so for
each due schedule as its owner (D134). None of it calls a model. The starter dashboard arrives with D3.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from nl2sql.conversations.store import Owner
from nl2sql.dashboards.edits import Applied, EditEnv, _title, apply_edits
from nl2sql.dashboards.models import (
    Arrange, ChartChoice, Dashboard, DashboardEdit, DashboardFilter, DashboardInfo, DashboardRun, DashboardView,
    MoveDashboard, RemoveTile, RenameDashboard, RenameTile, ScheduleIdentity, SetFilters, SetSchedule, SetTileChart,
    Tile, TileArrangement, TileRun,
)
from nl2sql.dashboards import schedule as scheduling
from nl2sql.dashboards.run import DashboardBusy, Refresher
from nl2sql.dashboards.store import DashboardStore, owner_for
from nl2sql.dashboards.workspaces import Workspaces
from nl2sql.events.catalog import DashboardChanged, DashboardScheduleSet, DashboardScheduleStopped
from nl2sql.hooks.approval import Approval
from nl2sql.policies import UserContext

logger = logging.getLogger(__name__)

_STATUSES = ("draft", "saved")
# A check that ends in one of these serves none of the chart's stored rows.
_BLOCKED = ("denied", "needs_fixing", "error")


class Dashboards:
    """What ``engine.dashboards`` does. ``DashboardsAPI`` is its public face."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._store = DashboardStore(ctx.state)
        self._workspaces = Workspaces(ctx, self._store)
        self._refresher = Refresher(ctx, self._store)

    def _owner(self, user: UserContext) -> Owner:
        return owner_for(user, self._ctx.tenant_id)

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
        dashboard = self.get(dashboard_id, user_context=user_context)
        with self._refresher.locked(dashboard_id, len(dashboard.tiles)):  # a refresh would store its results again
            self._store.delete(dashboard_id)
        self._changed(dashboard_id, ["deleted"], user_context)

    def pin(self, dashboard_id: str, *, user_context: UserContext, workspace_id: Optional[str] = None,
            title: Optional[str] = None) -> Dashboard:
        owner = self._owner(user_context)
        self._store.get(dashboard_id, owner)  # another owner's is not found, never busy
        with self._refresher.locked(dashboard_id):
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
                changes["title"] = _title(title, "dashboard")
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
        # Under the refresh lock: a refresh that finished after ``set_filters`` would store results that ignore
        # the new filters.
        with self._refresher.locked(dashboard_id):
            dashboard = self._store.get(dashboard_id, owner)
            now = datetime.now(timezone.utc)
            env = EditEnv(
                identity=ScheduleIdentity(user_id=user_context.user_id, tenant_id=user_context.tenant_id,
                                          roles=list(user_context.roles)),
                now=now, min_refresh_minutes=self._ctx.config.dashboards.min_refresh_minutes,
                workspace_ids={w.id for w in self._store.workspaces(owner)},
                chart_settings=self._ctx.settings.charts,
                last_run=lambda tile_id: self._store.last_run(dashboard_id, tile_id))
            applied = apply_edits(dashboard, edits, env)  # raises before anything is stored: all or none
            saved = applied.dashboard.model_copy(update={"updated_at": now})
            # Results first: if the drop fails nothing was saved, and a lost result only costs a refresh, whereas a
            # saved dashboard beside a stale result would show wrong data.
            self._store.drop_runs(dashboard_id, applied.dropped)
            self._store.put(saved)
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

    # ------------------------------------------------------------ open and refresh (D2)

    def open(self, dashboard_id: str, *, user_context: UserContext, refresh_due: bool = True) -> DashboardView:
        owner = self._owner(user_context)
        dashboard = self._store.get(dashboard_id, owner)
        last = self._last_runs(dashboard)
        # Stored results are served only after the check passes: a chart the caller can no longer read, or whose
        # plan no longer validates, comes back without its rows.
        checked = self._refresher.check([t for t in dashboard.tiles if t.id in last], user_context)
        blocked = {tile_id: run for tile_id, run in checked.items() if run.status in _BLOCKED}
        due = [tile_id for tile_id in scheduling.due_tiles(dashboard, last, datetime.now(timezone.utc))
               if tile_id not in blocked]
        if refresh_due and due:
            try:
                with self._refresher.locked(dashboard_id, len(dashboard.tiles)):
                    current = self._store.get(dashboard_id, owner)  # as it is now, under the lock
                    self._refresher.run(current, [t for t in current.tiles if t.id in due], user_context, "open")
            except DashboardBusy:
                pass  # someone else is refreshing it; the charts stay due
            last = self._last_runs(dashboard)
            due = [tile_id for tile_id in scheduling.due_tiles(dashboard, last, datetime.now(timezone.utc))
                   if tile_id not in blocked]
        served = [blocked.get(t.id) or last[t.id] for t in dashboard.tiles if t.id in blocked or t.id in last]
        passed = [run.at for run in served if run.tile_id not in blocked]
        return DashboardView(dashboard=dashboard, runs=served, refreshed_at=min(passed) if passed else None, due=due)

    def refresh(self, dashboard_id: str, *, user_context: UserContext, tile_id: Optional[str] = None,
                filters: Optional[Dict[str, List[Any]]] = None, approvals: Sequence[Approval] = ()) -> DashboardRun:
        owner = self._owner(user_context)
        dashboard = self._store.get(dashboard_id, owner)
        with self._refresher.locked(dashboard_id, len(dashboard.tiles)):
            dashboard = self._store.get(dashboard_id, owner)  # as it is now, under the lock
            tiles = self._chosen(dashboard, tile_id)
            return self._refresher.run(dashboard, tiles, user_context, "manual", approvals)

    @staticmethod
    def _chosen(dashboard: Dashboard, tile_id: Optional[str]) -> List[Tile]:
        if tile_id is None:
            return list(dashboard.tiles)
        chosen = [t for t in dashboard.tiles if t.id == tile_id]
        if not chosen:
            raise ValueError(f"No chart '{tile_id}' in this dashboard.")
        return chosen

    def _last_runs(self, dashboard: Dashboard) -> Dict[str, TileRun]:
        tiles = {t.id for t in dashboard.tiles}
        return {run.tile_id: run for run in self._store.runs(dashboard.id) if run.tile_id in tiles}

    # ------------------------------------------------------------ schedules (D2)

    def run_scheduled(self, *, now: Optional[datetime] = None) -> List[DashboardRun]:
        now = now or datetime.now(timezone.utc)
        done: List[DashboardRun] = []
        for doc in self._ctx.state.list_scheduled_dashboards():
            candidate = Dashboard.model_validate(doc)
            if not scheduling.is_due(candidate, now):
                continue
            try:
                with self._refresher.locked(candidate.id, len(candidate.tiles)):
                    run = self._scheduled(candidate.id, now)
            except DashboardBusy:
                continue  # another process is refreshing it
            except Exception:  # one dashboard's failure never stops the others
                logger.warning("The scheduled refresh of dashboard '%s' failed.", candidate.id, exc_info=True)
                continue
            if run is not None:
                done.append(run)
        return done

    def _scheduled(self, dashboard_id: str, now: datetime) -> Optional[DashboardRun]:
        """One due dashboard, under its lock: its checks, then its run as the owner saved on the schedule."""
        doc = self._ctx.state.get_dashboard(dashboard_id)
        dashboard = Dashboard.model_validate(doc) if doc else None
        if dashboard is None or not scheduling.is_due(dashboard, now):  # another process ran it first
            return None
        schedule = dashboard.schedule
        problem = scheduling.identity_problem(schedule, self._ctx.config.policies, now,
                                              self._ctx.config.dashboards.schedule_identity_max_age_days)
        if problem:
            self._stop(dashboard, *problem)
            return None
        run = self._refresher.run(dashboard, dashboard.tiles, scheduling.owner_as_saved(schedule.identity),
                                  "schedule")
        denied = [t for t, r in zip(dashboard.tiles, run.tiles) if r.status == "denied"]
        if denied:
            self._stop(dashboard, scheduling.ACCESS_DENIED, scheduling.denied_message(denied))
        else:
            self._store.put(dashboard.model_copy(update={"schedule": scheduling.ran(schedule, now)}))
        return run

    def _stop(self, dashboard: Dashboard, code: str, message: str) -> None:
        stopped = scheduling.stopped(dashboard.schedule, code, message)
        self._store.put(dashboard.model_copy(update={"schedule": stopped}))
        self._ctx.bus.publish("dashboard.schedule_stopped", DashboardScheduleStopped(
            dashboard_id=dashboard.id, reason=code), tenant_id=dashboard.tenant_id or self._ctx.tenant_id)

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
