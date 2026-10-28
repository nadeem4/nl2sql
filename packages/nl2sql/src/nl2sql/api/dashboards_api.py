"""``engine.dashboards``: dashboards pinned whole into workspaces (docs/architecture/wave_c.md, section 6).

A stub with its final signatures: D1 fills the store, the edits, ``pin`` and ``move``, D2 the refresh, the
stored results, the filters and the schedules, and D3 the starter dashboard. Every method takes the caller
keyword-only and required (D130), except ``run_scheduled``, which runs each dashboard as its stored owner (D134).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from nl2sql.common.planned import arrives_with
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

    # ---- D1: the store and the manual edits

    def get(self, dashboard_id: str, *, user_context: UserContext) -> Dashboard:
        """One of the caller's dashboards, draft or saved.

        Raises:
            DashboardNotFound: it does not exist or is not the caller's.
        """
        raise arrives_with("engine.dashboards.get", "D1")

    def list(self, *, user_context: UserContext, workspace_id: Optional[str] = None,
             status: str = "saved") -> List[DashboardInfo]:
        """The caller's dashboards, newest first."""
        raise arrives_with("engine.dashboards.list", "D1")

    def delete(self, dashboard_id: str, *, user_context: UserContext) -> None:
        """Deletes the dashboard, its stored results and its schedule."""
        raise arrives_with("engine.dashboards.delete", "D1")

    def pin(self, dashboard_id: str, *, user_context: UserContext, workspace_id: Optional[str] = None,
            title: Optional[str] = None) -> Dashboard:
        """Saves a draft whole, in ``workspace_id`` (else the default workspace). A single tile is never pinned."""
        raise arrives_with("engine.dashboards.pin", "D1")

    def edit(self, dashboard_id: str, edits: List[DashboardEdit], *, user_context: UserContext) -> Dashboard:
        """Applies a list of dashboard edits, all or none. The named methods below call it."""
        raise arrives_with("engine.dashboards.edit", "D1")

    def rename(self, dashboard_id: str, title: str, *, user_context: UserContext) -> Dashboard:
        """Renames the dashboard."""
        raise arrives_with("engine.dashboards.rename", "D1")

    def rename_tile(self, dashboard_id: str, tile_id: str, title: str, *, user_context: UserContext) -> Dashboard:
        """Renames one chart."""
        raise arrives_with("engine.dashboards.rename_tile", "D1")

    def set_tile_chart(self, dashboard_id: str, tile_id: str, chart: ChartChoice, *,
                       user_context: UserContext) -> Dashboard:
        """Sets how a chart is drawn; a kind its last result cannot draw is a ``ValueError`` naming the ones it can."""
        raise arrives_with("engine.dashboards.set_tile_chart", "D1")

    def remove_tile(self, dashboard_id: str, tile_id: str, *, user_context: UserContext) -> Dashboard:
        """Removes one chart and its stored result."""
        raise arrives_with("engine.dashboards.remove_tile", "D1")

    def arrange(self, dashboard_id: str, layouts: List[TileArrangement], *, user_context: UserContext) -> Dashboard:
        """Reorders or resizes charts on the grid."""
        raise arrives_with("engine.dashboards.arrange", "D1")

    def set_filters(self, dashboard_id: str, filters: List[DashboardFilter], *,
                    user_context: UserContext) -> Dashboard:
        """Stores the dashboard's filter definitions."""
        raise arrives_with("engine.dashboards.set_filters", "D1")

    def set_schedule(self, dashboard_id: str, every_minutes: Optional[int], *,
                     user_context: UserContext) -> Dashboard:
        """Sets or (with None) removes the refresh schedule, saving the caller's identity (D134).

        Raises:
            ScheduleTooFrequent: below ``dashboards.min_refresh_minutes``.
        """
        raise arrives_with("engine.dashboards.set_schedule", "D1")

    def move(self, dashboard_id: str, workspace_id: str, *, user_context: UserContext) -> Dashboard:
        """Moves the dashboard to another of the caller's workspaces."""
        raise arrives_with("engine.dashboards.move", "D1")

    # ---- D2: refresh, stored results, filters and schedules

    def open(self, dashboard_id: str, *, user_context: UserContext, refresh_due: bool = True) -> DashboardView:
        """Checks access first, then returns the stored results; with ``refresh_due``, charts older than the
        schedule are refreshed first."""
        raise arrives_with("engine.dashboards.open", "D2")

    def refresh(self, dashboard_id: str, *, user_context: UserContext, tile_id: Optional[str] = None,
                filters: Optional[Dict[str, List[Any]]] = None,
                approvals: Sequence[Approval] = ()) -> DashboardRun:
        """Re-runs the stored plans of one chart or of all of them, as the caller, and stores the results.
        No model call."""
        raise arrives_with("engine.dashboards.refresh", "D2")

    def filter_values(self, dashboard_id: str, filter_id: str, *, user_context: UserContext,
                      limit: int = 100) -> List[Any]:
        """The distinct values of an ``in`` filter's column, or ``[min, max]`` of a ``between`` one."""
        raise arrives_with("engine.dashboards.filter_values", "D2")

    def run_scheduled(self, *, now: Optional[datetime] = None) -> List[DashboardRun]:
        """Refreshes every dashboard whose schedule is due, each as its stored owner (D134). It has no caller;
        only a long-running process and ``nl2sql dashboards refresh --due`` call it."""
        raise arrives_with("engine.dashboards.run_scheduled", "D2")

    # ---- D3: the starter dashboard

    def generate(self, datasource_id: str, *, user_context: UserContext, title: Optional[str] = None,
                 workspace_id: Optional[str] = None,
                 max_tiles: Optional[int] = None) -> Tuple[Dashboard, DashboardRun]:
        """The no-model starter (D115): charts chosen from the column roles, run as the caller, laid out and saved."""
        raise arrives_with("engine.dashboards.generate", "D3")

    def suggest_questions(self, datasource_id: str, *, user_context: UserContext, n: int = 5) -> List[str]:
        """The questions of the first ``n`` charts ``generate`` would choose, without running them."""
        raise arrives_with("engine.dashboards.suggest_questions", "D3")
