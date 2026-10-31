"""The dashboards service behind ``engine.dashboards`` (docs/architecture/wave_c.md, section 6).

D1 is the store and the manual edits: a dashboard is private to its owner (D118), lives in one of their
workspaces (D131), is built as a draft and saved whole by ``pin``. D2 runs the charts: ``open`` serves the stored
results after the access check, ``refresh`` re-runs the stored plans as the caller, ``run_scheduled`` does so for
each due schedule as its owner (D134). D3 is the starter dashboard: ``generate`` builds the template charts from the
column roles, runs each as the caller and saves the ones that answered. D4 lets a conversation build a draft and add or
replace a chart from an answer it holds, after a no-execute check of the plan. None of it calls a model.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from nl2sql.common.errors import InvalidArgument, NoTiles, WorkspaceNotEmpty
from nl2sql.conversations.models import Conversation
from nl2sql.conversations.store import ConversationStore, Owner
from nl2sql.dashboards.edits import Applied, EditEnv, _title, apply_edits, plan_from_step
from nl2sql.dashboards import filters as filtering
from nl2sql.dashboards.layout import arrange
from nl2sql.dashboards.models import (
    Arrange, ChartChoice, Dashboard, DashboardEdit, DashboardFilter, DashboardInfo, DashboardRun, DashboardView,
    MoveDashboard, RemoveTile, RenameDashboard, RenameTile, ScheduleIdentity, SetFilters, SetSchedule, SetTileChart,
    SkippedTile, Tile, TileArrangement, TileLayout, TileRun,
)
from nl2sql.dashboards import schedule as scheduling
from nl2sql.dashboards.roles import Source, column_roles
from nl2sql.dashboards.run import KEEPS_LAST, SERVES, DashboardBusy, Refresher
from nl2sql.dashboards.store import DashboardStore, owner_for
from nl2sql.dashboards.templates import Candidate, candidates, filters_for
from nl2sql.dashboards.workspaces import Workspaces
from nl2sql.events.catalog import DashboardChanged, DashboardGenerated, DashboardScheduleSet, DashboardScheduleStopped
from nl2sql.hooks.approval import Approval
from nl2sql.policies import UserContext

logger = logging.getLogger(__name__)

_STATUSES = ("draft", "saved")
_PLACE = TileLayout(x=0, y=0, w=1, h=1)  # a starter chart's place until the layout puts it somewhere
DEFAULT_DRAFT_TITLE = "New dashboard"


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
            raise InvalidArgument(f"status must be one of {', '.join(_STATUSES)}, not '{status}'.")
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

    def create_draft(self, *, user_context: UserContext, title: Optional[str] = None,
                     workspace_id: Optional[str] = None, conversation_id: Optional[str] = None) -> Dashboard:
        """An empty draft owned by the caller, in ``workspace_id`` (else "General", created on first use). The chat
        tool makes one on first use and ties it to its conversation."""
        owner = self._owner(user_context)
        if conversation_id is not None:
            ConversationStore(self._ctx.state).get(conversation_id, owner)  # another caller's: not found
        name = _title(title if title is not None else DEFAULT_DRAFT_TITLE, "dashboard")
        workspace = self._store.workspace(workspace_id, owner) if workspace_id is not None \
            else self._workspaces.default(owner, user_context)
        now = datetime.now(timezone.utc)
        draft = Dashboard(id=secrets.token_urlsafe(12), title=name, workspace_id=workspace.id, status="draft",
                          conversation_id=conversation_id, tenant_id=owner[0], user_id=owner[1], created_at=now,
                          updated_at=now)
        self._store.put(draft)
        self._changed(draft.id, ["created"], user_context)
        return draft

    def pin(self, dashboard_id: str, *, user_context: UserContext, workspace_id: Optional[str] = None,
            title: Optional[str] = None) -> Dashboard:
        owner = self._owner(user_context)
        self._store.get(dashboard_id, owner)  # another owner's is not found, never busy
        with self._refresher.locked(dashboard_id):
            dashboard = self._store.get(dashboard_id, owner)
            if dashboard.status == "saved":
                raise InvalidArgument(f"Dashboard '{dashboard_id}' is already saved.")
            if not dashboard.tiles:
                raise InvalidArgument("An empty draft cannot be pinned: add a chart first.")
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

    def edit(self, dashboard_id: str, edits: Sequence[DashboardEdit], *, user_context: UserContext,
             conversation: Optional[Conversation] = None) -> Dashboard:
        owner = self._owner(user_context)
        dashboard = self._store.get(dashboard_id, owner)
        if not edits:
            return dashboard
        if conversation is not None and (conversation.tenant_id, conversation.user_id) != owner:
            raise InvalidArgument("That conversation is not the caller's: its answers cannot be added to a dashboard.")
        # Under the refresh lock: a refresh that finished after ``set_filters`` would store results that ignore
        # the new filters.
        checks = sum(1 for e in edits if e.kind in ("add_tile", "replace_plan"))  # each runs a no-execute check
        with self._refresher.locked(dashboard_id, checks):
            dashboard = self._store.get(dashboard_id, owner)
            now = datetime.now(timezone.utc)
            env = EditEnv(
                identity=ScheduleIdentity(user_id=user_context.user_id, tenant_id=user_context.tenant_id,
                                          roles=list(user_context.roles)),
                now=now, min_refresh_minutes=self._ctx.config.dashboards.min_refresh_minutes,
                workspace_ids={w.id for w in self._store.workspaces(owner)},
                chart_settings=self._ctx.settings.charts,
                last_run=lambda tile_id: self._store.last_run(dashboard_id, tile_id),
                step=None if conversation is None else (lambda ref: plan_from_step(conversation, ref)),
                check=lambda tile: self._check(tile, user_context))
            applied = apply_edits(dashboard, edits, env)  # raises before anything is stored: all or none
            saved = applied.dashboard.model_copy(update={"updated_at": now})
            # Results first: if the drop fails nothing was saved, and a lost result only costs a refresh, whereas a
            # saved dashboard beside a stale result would show wrong data.
            self._store.drop_runs(dashboard_id, applied.dropped)
            self._store.put(saved)
        self._published(applied, saved, user_context)
        return saved

    def _check(self, tile: Tile, user: UserContext) -> None:
        """The access check of D135 for a chart about to be saved: its plan is validated and generated as the caller,
        running nothing. A plan the validator rejects, or that the caller may not read, never lands (D4)."""
        run = self._refresher.check([tile], user)[tile.id]
        if run.status not in SERVES:
            why = (run.error or {}).get("message") or f"the check ended {run.status}"
            raise InvalidArgument(f"The plan of the chart '{tile.title}' was refused ({why}). Nothing was saved.")

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
        # Stored results are served only after the check passes (an allow-list: ``SERVES``): a chart the caller can
        # no longer read, or whose plan no longer validates, comes back without its rows.
        checked = self._refresher.check([t for t in dashboard.tiles if t.id in last], user_context)
        blocked = {tile_id: run for tile_id, run in checked.items() if run.status not in SERVES}
        # Only what this open checked, or refreshed itself, is served: a result another process stored meanwhile was
        # never checked for this caller.
        passed = {tile_id: run for tile_id, run in last.items() if tile_id not in blocked}
        due = self._due(dashboard, passed, blocked)
        if refresh_due and due:
            try:
                with self._refresher.locked(dashboard_id, len(dashboard.tiles)):
                    current = self._store.get(dashboard_id, owner)  # as it is now, under the lock
                    run = self._refresher.run(current, [t for t in current.tiles if t.id in due], user_context,
                                              "open")
                passed.update({r.tile_id: r for r in run.tiles if r.status not in KEEPS_LAST})
            except DashboardBusy:
                pass  # someone else is refreshing it; the charts stay due
            due = self._due(dashboard, passed, blocked)
        served = [blocked.get(t.id) or passed[t.id] for t in dashboard.tiles if t.id in blocked or t.id in passed]
        ats = [run.at for run in served if run.tile_id not in blocked]
        return DashboardView(dashboard=dashboard, runs=served, refreshed_at=min(ats) if ats else None, due=due)

    @staticmethod
    def _due(dashboard: Dashboard, passed: Dict[str, TileRun], blocked: Dict[str, TileRun]) -> List[str]:
        return [tile_id for tile_id in scheduling.due_tiles(dashboard, passed, datetime.now(timezone.utc))
                if tile_id not in blocked]

    def refresh(self, dashboard_id: str, *, user_context: UserContext, tile_id: Optional[str] = None,
                filters: Optional[Dict[str, List[Any]]] = None, approvals: Sequence[Approval] = ()) -> DashboardRun:
        owner = self._owner(user_context)
        dashboard = self._store.get(dashboard_id, owner)
        active = filtering.activate(dashboard.filters, filters)  # refused before anything runs
        if active:  # a view for whoever asked: nothing is stored, so there is nothing to lock
            return self._refresher.run(dashboard, self._chosen(dashboard, tile_id), user_context, "manual",
                                       approvals=approvals, active=active)
        with self._refresher.locked(dashboard_id, len(dashboard.tiles)):
            dashboard = self._store.get(dashboard_id, owner)  # as it is now, under the lock
            return self._refresher.run(dashboard, self._chosen(dashboard, tile_id), user_context, "manual",
                                       approvals=approvals)

    def filter_values(self, dashboard_id: str, filter_id: str, *, user_context: UserContext,
                      limit: int = 100) -> List[Any]:
        dashboard = self._store.get(dashboard_id, self._owner(user_context))
        f = filtering.find(dashboard.filters, filter_id)
        if not 1 <= limit <= filtering.MAX_VALUES_LIMIT:
            raise InvalidArgument(f"limit must be between 1 and {filtering.MAX_VALUES_LIMIT}, not {limit}.")
        return self._refresher.values(f, user_context, limit)

    @staticmethod
    def _chosen(dashboard: Dashboard, tile_id: Optional[str]) -> List[Tile]:
        if tile_id is None:
            return list(dashboard.tiles)
        chosen = [t for t in dashboard.tiles if t.id == tile_id]
        if not chosen:
            raise InvalidArgument(f"No chart '{tile_id}' in this dashboard.")
        return chosen

    def _last_runs(self, dashboard: Dashboard) -> Dict[str, TileRun]:
        tiles = {t.id for t in dashboard.tiles}
        return {run.tile_id: run for run in self._store.runs(dashboard.id) if run.tile_id in tiles}

    # ------------------------------------------------------------ schedules (D2)

    def run_scheduled(self, *, now: Optional[datetime] = None) -> List[DashboardRun]:
        if now is not None and now.utcoffset() is None:
            raise InvalidArgument("now must carry a timezone, such as datetime.now(timezone.utc).")
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
        denied = [(t, r) for t, r in zip(dashboard.tiles, run.tiles) if r.status == "denied"]
        if denied:
            self._stop(dashboard, *scheduling.denied(denied))
        else:
            self._store.put(dashboard.model_copy(update={"schedule": scheduling.ran(schedule, now)}))
        return run

    def _stop(self, dashboard: Dashboard, code: str, message: str) -> None:
        stopped = scheduling.stopped(dashboard.schedule, code, message)
        self._store.put(dashboard.model_copy(update={"schedule": stopped}))
        self._ctx.bus.publish("dashboard.schedule_stopped", DashboardScheduleStopped(
            dashboard_id=dashboard.id, reason=code), tenant_id=dashboard.tenant_id or self._ctx.tenant_id)

    # ------------------------------------------------------------ the starter dashboard (D3)

    def generate(self, source: Source, *, user_context: UserContext, title: Optional[str] = None,
                 workspace_id: Optional[str] = None, max_tiles: Optional[int] = None) -> Tuple[Dashboard, DashboardRun]:
        """The no-model starter (D115): the template charts for ``source``, each run as the caller, the ones that
        answered kept, laid out and saved whole. A generated dashboard is an ordinary one."""
        owner = self._owner(user_context)
        if workspace_id is not None:
            self._store.workspace(workspace_id, owner)  # before anything runs
        chosen = self._chosen_templates(source, max_tiles)
        had = {w.id for w in self._store.workspaces(owner)}
        workspace = self._store.workspace(workspace_id, owner) if workspace_id else self._workspaces.default(
            owner, user_context)
        made = workspace.id not in had  # this call made "General": a call that keeps nothing takes it away again
        now = datetime.now(timezone.utc)
        by_id = {f"t{n}": c for n, c in enumerate(chosen, 1)}  # ids follow the order they were chosen in
        tiles = arrange([Tile(id=i, title=c.title, question=c.question, plan=c.plan, layout=_PLACE)
                         for i, c in by_id.items()], {i: c.kind for i, c in by_id.items()})
        draft = Dashboard(id=secrets.token_urlsafe(12), title=(title or "").strip() or f"{source.datasource_id} overview",
                          workspace_id=workspace.id, status="draft", tenant_id=owner[0], user_id=owner[1], tiles=tiles,
                          created_at=now, updated_at=now)
        self._store.put(draft)
        try:
            with self._refresher.locked(draft.id, len(tiles)):
                dashboard, run = self._keep(draft, self._refresher.run(draft, draft.tiles, user_context, "generate",
                                                                      announce=False), by_id)
        except BaseException:
            self._store.delete(draft.id)  # nothing half-made is left behind
            if made:
                try:
                    self._workspaces.delete(workspace.id, user_context=user_context)
                except WorkspaceNotEmpty:  # someone filed a dashboard there meanwhile
                    pass
            raise
        self._ctx.bus.publish("dashboard.generated", DashboardGenerated(
            dashboard_id=dashboard.id, datasource_id=source.datasource_id, tiles=len(dashboard.tiles),
            skipped=len(run.skipped)), tenant_id=owner[0])
        return dashboard, run

    def suggest_questions(self, source: Source, *, n: int = 5) -> List[str]:
        """The questions of the first ``n`` charts ``generate`` would choose. Nothing runs and nothing is saved."""
        if n < 1:
            raise InvalidArgument("n must be at least 1.")
        try:
            return [c.question for c in self._chosen_templates(source, None)][:n]
        except NoTiles:
            return []

    def _chosen_templates(self, source: Source, max_tiles: Optional[int]) -> List[Candidate]:
        if max_tiles is not None and max_tiles < 1:
            raise InvalidArgument("max_tiles must be at least 1.")
        tables = column_roles(source)
        if not tables:
            raise NoTiles(f"'{source.datasource_id}' has no table you can read, so there is nothing to chart.")
        return candidates(source.datasource_id, tables)[:max_tiles or self._ctx.config.dashboards.max_tiles]

    def _keep(self, draft: Dashboard, ran: DashboardRun, chosen: Dict[str, Candidate]
              ) -> Tuple[Dashboard, DashboardRun]:
        """Keep the charts whose first run answered (``ok``); list the others as skipped and drop their results."""
        kept = [r for r in ran.tiles if r.status == "ok"]
        skipped = [SkippedTile(title=t.title, status=r.status, reason=(r.error or {}).get("message") or
                               f"The chart ended {r.status}.") for t, r in zip(draft.tiles, ran.tiles)
                   if r.status != "ok"]
        if not kept:
            reasons = "; ".join(f"{s.title}: {s.reason}" for s in skipped)
            raise NoTiles(f"None of the {len(draft.tiles)} starter charts answered ({reasons}).")
        self._refresher.announce(draft, ran)  # only a dashboard that is kept is announced
        self._store.drop_runs(draft.id, [r.tile_id for r in ran.tiles if r.status != "ok"])
        tiles_by_id = {t.id: t for t in draft.tiles}
        tiles = arrange([tiles_by_id[r.tile_id] for r in kept], {r.tile_id: r.chart.kind for r in kept if r.chart})
        saved = draft.model_copy(update={
            "tiles": tiles, "status": "saved", "updated_at": datetime.now(timezone.utc),
            "filters": filters_for([chosen[t.id] for t in tiles])})
        self._store.put(saved)
        return saved, ran.model_copy(update={"tiles": kept, "skipped": skipped})

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
