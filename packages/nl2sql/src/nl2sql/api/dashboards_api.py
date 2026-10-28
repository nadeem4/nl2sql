"""``engine.dashboards``: dashboards pinned whole into workspaces (docs/architecture/wave_c.md, section 6).

The logic is ``nl2sql.dashboards.api``. D1 filled the store, the edits, ``pin`` and ``move``; ``open``,
``refresh``, ``filter_values`` and ``run_scheduled`` are stubs for D2 (the refresh, the stored results, the
filters and the schedules) and ``generate`` and ``suggest_questions`` for D3 (the starter dashboard). Every
method takes the caller keyword-only and required (D130), except ``run_scheduled``, which runs each dashboard as its stored owner (D134).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from nl2sql.common.planned import arrives_with
from nl2sql.dashboards.api import Dashboards
from nl2sql.dashboards.models import (
    ChartChoice,
    Dashboard,
    DashboardEdit,
    DashboardFilter,
    DashboardInfo,
    DashboardRun,
    DashboardView,
    TileArrangement,
)
from nl2sql.hooks.approval import Approval
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime


class DashboardsAPI:
    """``engine.dashboards``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx
        self._service = Dashboards(ctx)

    # ---- D1: the store and the manual edits

    def get(self, dashboard_id: str, *, user_context: UserContext) -> Dashboard:
        """One of the caller's dashboards, draft or saved.

        Raises:
            DashboardNotFound: it does not exist or is not the caller's.
        """
        return self._service.get(dashboard_id, user_context=user_context)

    def list(self, *, user_context: UserContext, workspace_id: Optional[str] = None,
             status: str = "saved") -> List[DashboardInfo]:
        """The caller's dashboards of one ``status`` (``saved`` or ``draft``), newest first."""
        return self._service.list(user_context=user_context, workspace_id=workspace_id, status=status)

    def delete(self, dashboard_id: str, *, user_context: UserContext) -> None:
        """Deletes the dashboard, its stored results and its schedule."""
        self._service.delete(dashboard_id, user_context=user_context)

    def pin(self, dashboard_id: str, *, user_context: UserContext, workspace_id: Optional[str] = None,
            title: Optional[str] = None) -> Dashboard:
        """Saves a draft whole, in ``workspace_id`` (else the workspace the draft is in, else "General", created
        on first use). A single tile is never pinned; an empty draft or a saved dashboard is a ``ValueError``."""
        return self._service.pin(dashboard_id, user_context=user_context, workspace_id=workspace_id, title=title)

    def edit(self, dashboard_id: str, edits: List[DashboardEdit], *, user_context: UserContext) -> Dashboard:
        """Applies a list of dashboard edits, all or none. The named methods below call it. ``add_tile`` and
        ``replace_plan`` change what data a chart shows and arrive with D4 (chat only)."""
        return self._service.edit(dashboard_id, edits, user_context=user_context)

    def rename(self, dashboard_id: str, title: str, *, user_context: UserContext) -> Dashboard:
        """Renames the dashboard."""
        return self._service.rename(dashboard_id, title, user_context=user_context)

    def rename_tile(self, dashboard_id: str, tile_id: str, title: str, *, user_context: UserContext) -> Dashboard:
        """Renames one chart."""
        return self._service.rename_tile(dashboard_id, tile_id, title, user_context=user_context)

    def set_tile_chart(self, dashboard_id: str, tile_id: str, chart: ChartChoice, *,
                       user_context: UserContext) -> Dashboard:
        """Sets how a chart is drawn; a kind its last result cannot draw is a ``ValueError`` naming the ones it can."""
        return self._service.set_tile_chart(dashboard_id, tile_id, chart, user_context=user_context)

    def remove_tile(self, dashboard_id: str, tile_id: str, *, user_context: UserContext) -> Dashboard:
        """Removes one chart and its stored result."""
        return self._service.remove_tile(dashboard_id, tile_id, user_context=user_context)

    def arrange(self, dashboard_id: str, layouts: List[TileArrangement], *, user_context: UserContext) -> Dashboard:
        """Reorders or resizes charts on the grid."""
        return self._service.arrange(dashboard_id, layouts, user_context=user_context)

    def set_filters(self, dashboard_id: str, filters: List[DashboardFilter], *,
                    user_context: UserContext) -> Dashboard:
        """Stores the dashboard's filter definitions (D2 applies their values)."""
        return self._service.set_filters(dashboard_id, filters, user_context=user_context)

    def set_schedule(self, dashboard_id: str, every_minutes: Optional[int], *,
                     user_context: UserContext) -> Dashboard:
        """Sets or (with None) removes the refresh schedule, saving the caller's identity (D134). Nothing runs
        until D2's scheduler does.

        Raises:
            ScheduleTooFrequent: below ``dashboards.min_refresh_minutes``.
        """
        return self._service.set_schedule(dashboard_id, every_minutes, user_context=user_context)

    def move(self, dashboard_id: str, workspace_id: str, *, user_context: UserContext) -> Dashboard:
        """Moves the dashboard to another of the caller's workspaces."""
        return self._service.move(dashboard_id, workspace_id, user_context=user_context)

    # ---- D2: refresh, stored results, filters and schedules

    def open(self, dashboard_id: str, *, user_context: UserContext, refresh_due: bool = True) -> DashboardView:
        """Checks access first, then returns the stored results of the charts that passed.

        Each stored plan is checked under the caller's current scope (a generated plan is validated and generated,
        not run; a verified query is run, where the matcher is). A chart the caller can no longer read comes back
        ``denied`` and one whose plan no longer validates ``needs_fixing``, both without rows. With
        ``refresh_due`` (the default) the charts older than the schedule are then refreshed, as the caller and
        never with approvals; an app that wants an instant open passes ``False`` and refreshes the ids in ``due``
        itself. A dashboard being refreshed by someone else leaves its charts due.

        Raises:
            DashboardNotFound: it does not exist or is not the caller's.
        """
        return self._service.open(dashboard_id, user_context=user_context, refresh_due=refresh_due)

    def refresh(self, dashboard_id: str, *, user_context: UserContext, tile_id: Optional[str] = None,
                filters: Optional[Dict[str, List[Any]]] = None,
                approvals: Sequence[Approval] = ()) -> DashboardRun:
        """Re-runs the stored plans of one chart (``tile_id``) or of all of them, in layout order, as the caller,
        and stores the results. No model call.

        A chart whose plan no longer validates is ``needs_fixing`` (no rows, the refiner is never asked); one the
        caller cannot read is ``denied``; one a hook wants approved is ``needs_approval`` with its requests in
        ``approvals_needed`` and keeps its last result; pass an ``Approval`` for each in ``approvals`` to run it.
        A chart stopped at a cap ends the refresh.

        Raises:
            DashboardNotFound: it does not exist or is not the caller's.
            DashboardBusy: (a ``ValueError``) another caller or process is refreshing or editing it.
            ValueError: ``tile_id`` is not one of its charts.
        """
        if filters:
            raise arrives_with("engine.dashboards.refresh(filters=...)", "D2b")
        return self._service.refresh(dashboard_id, user_context=user_context, tile_id=tile_id, filters=filters,
                                     approvals=approvals)

    def filter_values(self, dashboard_id: str, filter_id: str, *, user_context: UserContext,
                      limit: int = 100) -> List[Any]:
        """The distinct values of an ``in`` filter's column, or ``[min, max]`` of a ``between`` one."""
        raise arrives_with("engine.dashboards.filter_values", "D2b")

    def run_scheduled(self, *, now: Optional[datetime] = None) -> List[DashboardRun]:
        """Refreshes every dashboard whose schedule is due, each as its stored owner (D134). It has no caller;
        only a long-running process and ``nl2sql dashboards refresh --due`` call it.

        Each dashboard is refreshed under its lock (one being refreshed is skipped). Before it runs, the saved
        identity is checked: a schedule older than ``dashboards.schedule_identity_max_age_days``, or one that
        names a role the policies no longer define, stops; so does one with a chart its owner can no longer read.
        A stopped schedule has ``state="stopped"`` and an ``error`` (``code`` and ``message``) the owner sees on
        the dashboard, ``dashboard.schedule_stopped`` is published, and nothing retries it: setting the schedule
        again saves a fresh snapshot. A scheduled run never approves. Returns the dashboards that ran.
        """
        return self._service.run_scheduled(now=now)

    # ---- D3: the starter dashboard

    def generate(self, datasource_id: str, *, user_context: UserContext, title: Optional[str] = None,
                 workspace_id: Optional[str] = None,
                 max_tiles: Optional[int] = None) -> Tuple[Dashboard, DashboardRun]:
        """The no-model starter (D115): charts chosen from the column roles, run as the caller, laid out and saved."""
        raise arrives_with("engine.dashboards.generate", "D3")

    def suggest_questions(self, datasource_id: str, *, user_context: UserContext, n: int = 5) -> List[str]:
        """The questions of the first ``n`` charts ``generate`` would choose, without running them."""
        raise arrives_with("engine.dashboards.suggest_questions", "D3")
