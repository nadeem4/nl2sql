"""``engine.dashboards``: dashboards pinned whole into workspaces (docs/architecture/wave_c.md, section 6).

The logic is ``nl2sql.dashboards.api``. D1 filled the store, the edits, ``pin`` and ``move``; D2 ``open``,
``refresh`` and ``run_scheduled``; D2b ``filter_values`` and the filters of ``refresh``; D3 the starter dashboard,
``generate`` and ``suggest_questions``; D4 lets a conversation build and edit a draft (``create_draft``, and ``edit``
with a conversation). Every method takes the caller keyword-only and required (D130), except ``run_scheduled``, which
runs each dashboard as its stored owner (D134).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from nl2sql.api.types import TileProgress
from nl2sql.approvals.api import Approvals
from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.errors import ApprovalNotFound, NotIndexed
from nl2sql.common.planned import arrives_with
from nl2sql.conversations.models import Conversation
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
from nl2sql.dashboards.roles import Source
from nl2sql.hooks.approval import Approval
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime


def resolve_refresh_approvals(ctx: Runtime, approvals: Sequence[Union[Approval, str]],
                              user: UserContext) -> List[Approval]:
    """``approvals`` for a refresh, each request id replaced by its grant (D236). An id that cannot run is an
    ``ApprovalNotFound`` before anything refreshes: one bad id must not fail every chart with the same error."""
    if all(isinstance(a, Approval) for a in approvals):
        return list(approvals)
    resolved, problem = Approvals(ctx).resolve(approvals, user=user)
    if problem is not None:
        raise ApprovalNotFound(problem)
    return resolved


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

    def create_draft(self, *, user_context: UserContext, title: Optional[str] = None,
                     workspace_id: Optional[str] = None, conversation_id: Optional[str] = None) -> Dashboard:
        """An empty draft owned by the caller (title "New dashboard" unless given), in ``workspace_id`` or else
        "General", created on first use. ``dashboard_edit`` makes one on first use in a conversation and
        ``conversation_id`` ties it to that conversation; ``pin`` saves it.

        Raises:
            WorkspaceNotFound: ``workspace_id`` is not one of the caller's.
            ConversationNotFound: ``conversation_id`` is not one of the caller's conversations.
        """
        return self._service.create_draft(user_context=user_context, title=title, workspace_id=workspace_id,
                                          conversation_id=conversation_id)

    def edit(self, dashboard_id: str, edits: List[DashboardEdit], *, user_context: UserContext,
             conversation: Optional[Conversation] = None) -> Dashboard:
        """Applies a list of dashboard edits, all or none. The named methods below call it.

        ``add_tile`` and ``replace_plan`` change what data a chart shows, so they exist only in a conversation: they
        read the plan of an ``ask`` step of ``conversation`` (the caller's own; the chat tool passes the one it is
        in), and a plan the validator rejects, or the caller may not read, is refused by a run that executes nothing
        before anything is saved. Without ``conversation`` they are a ``ValueError``.

        ``conversation`` is trusted: its steps are not re-read from the store, only its owner is checked against the
        caller. So it is for SDK code that holds the caller's own ``Conversation`` (the chat tool does), and is not
        offered over REST or MCP. The access check of the plan still runs as the caller. In ``from_step``, ``turn``
        defaults to the conversation's latest turn."""
        return self._service.edit(dashboard_id, edits, user_context=user_context, conversation=conversation)

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
        """Stores the dashboard's filter definitions; ``refresh(filters=...)`` applies their values."""
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

        Each stored plan is checked under the caller's current scope, running no query (a generated plan is
        validated and generated; a verified query is checked by the curated matcher). Rows are served only when
        the check ends ``ok``, ``empty``, or ``needs_approval`` with nothing else wrong. A chart the caller can no
        longer read (any part of it) comes back ``denied`` and one whose plan no longer validates ``needs_fixing``,
        both without rows. Only charts this call checked or refreshed are served. With
        ``refresh_due`` (the default) the charts older than the schedule are then refreshed, as the caller and
        never with approvals; an app that wants an instant open passes ``False`` and refreshes the ids in ``due``
        itself. A dashboard being refreshed by someone else leaves its charts due.

        Raises:
            DashboardNotFound: it does not exist or is not the caller's.
        """
        return self._service.open(dashboard_id, user_context=user_context, refresh_due=refresh_due)

    def refresh(self, dashboard_id: str, *, user_context: UserContext, tile_id: Optional[str] = None,
                filters: Optional[Dict[str, List[Any]]] = None,
                approvals: Sequence[Union[Approval, str]] = ()) -> DashboardRun:
        """Re-runs the stored plans of one chart (``tile_id``) or of all of them, in layout order, as the caller,
        and stores the results. No model call.

        A chart whose plan no longer validates is ``needs_fixing`` (no rows, the refiner is never asked); one the
        caller cannot read is ``denied``; one a hook wants approved is ``needs_approval`` with its requests in
        ``approvals_needed`` and keeps its last result; pass an ``Approval`` or a granted request id (``engine.approvals``,
        D236) for each in ``approvals`` to run it.
        A chart whose run fails (``error``) keeps its last good result too. A chart stopped at a cap ends the
        refresh.

        ``filters`` maps filter ids to values (D117). Each is checked against the filter's kind before anything
        runs, then added as a predicate (``between [a, b]`` is two comparisons, ``in`` is ORs, the values plan
        literals) to every stored query that reads the filter's table; a query that does not read it runs
        unfiltered, and a chart is ``filtered`` only when every active filter reached one of its queries. A
        filtered refresh is returned and never stored: the stored results stay unfiltered.

        Raises:
            DashboardNotFound: it does not exist or is not the caller's.
            DashboardBusy: (a ``ValueError``) another caller or process is refreshing or editing it.
            ApprovalNotFound: an id in ``approvals`` is unknown, waiting, denied or expired; nothing runs.
            ValueError: ``tile_id`` is not one of its charts, a filter id is not the dashboard's, or a value has
                the wrong type or count (the message names the filter).
        """
        return self._service.refresh(dashboard_id, user_context=user_context, tile_id=tile_id, filters=filters,
                                     approvals=resolve_refresh_approvals(self._ctx, approvals, user_context))

    def filter_values(self, dashboard_id: str, filter_id: str, *, user_context: UserContext,
                      limit: int = 100) -> List[Any]:
        """The distinct values of an ``in`` filter's column (up to ``limit``, 1 to 1,000, in order), or ``[min, max]``
        of a ``between`` one; ``[]`` when there are none. A plan built in code, run as the caller through the same
        supplied-plan path as a chart (the validator applies the caller's scope and policies), with no model call.
        Dates come back as ISO text, so a value can be passed straight to ``refresh(filters=...)``.

        Raises:
            DashboardNotFound: it does not exist or is not the caller's.
            ValueError: no such filter, ``limit`` out of range, or the caller cannot read the column (the message
                says why).
        """
        return self._service.filter_values(dashboard_id, filter_id, user_context=user_context, limit=limit)

    def run_scheduled(self, *, now: Optional[datetime] = None) -> List[DashboardRun]:
        """Refreshes every dashboard whose schedule is due, each as its stored owner (D134). It has no caller;
        only a long-running process and ``nl2sql dashboards refresh --due`` call it.

        Each dashboard is refreshed under its lock (one being refreshed is skipped). Before it runs, the saved
        identity is checked: a schedule older than ``dashboards.schedule_identity_max_age_days``, or one that
        names a role the policies no longer define, stops; so does one with a chart its owner can no longer read
        (``SCHEDULE_ACCESS_DENIED``) or a hook denied (``SCHEDULE_HOOK_DENIED``).
        A stopped schedule has ``state="stopped"`` and an ``error`` (``code`` and ``message``) the owner sees on
        the dashboard, ``dashboard.schedule_stopped`` is published, and nothing retries it: setting the schedule
        again saves a fresh snapshot. A scheduled run never approves. Returns the dashboards that ran.

        Raises:
            ValueError: ``now`` has no timezone.
        """
        return self._service.run_scheduled(now=now)

    # ---- D3: the starter dashboard

    def generate(self, datasource_id: str, *, user_context: UserContext, title: Optional[str] = None,
                 workspace_id: Optional[str] = None,
                 max_tiles: Optional[int] = None, on_tile: Optional[Callable[[TileProgress], None]] = None,
                 cancellation_token: Optional[CancellationToken] = None) -> Tuple[Dashboard, DashboardRun]:
        """The no-model starter (D115): charts chosen from the column roles, run as the caller, laid out and saved.

        Needs an index, not a profile. Each column's role comes from Wave B's rules over the indexed snapshot, and a
        role in the glossary wins. Four templates (key totals, trends, top N, distributions) build typed plans in
        code; each runs through ``run_query`` as the caller, so only the tables and columns the caller can read are
        charted and every hook and the validator apply. The charts whose first run is ``ok`` are kept (up to
        ``max_tiles``, else ``dashboards.max_tiles``); the others are listed in ``DashboardRun.skipped``. The
        dashboard is saved, owned by the caller, in ``workspace_id`` (else the caller's "General" workspace, created
        on first use) with a filter on the time column the trends use and on the dimensions the distributions use.
        No model is called.

        ``on_tile`` gets a :class:`~nl2sql.TileProgress` after each chart ran (its title, status and ``done`` of
        ``total``). Cancel ``cancellation_token`` to stop between charts: the chart in flight finishes and the call
        raises ``RunCancelled`` with nothing saved, no draft and no workspace it made.

        Raises:
            NotIndexed: the datasource has no index, or the caller cannot reach it (the same error, so the answer says
                nothing about a datasource the caller may not see); the message names the command.
            NoTiles: the caller reaches no table, or no chart answered; the message says what is missing.
            WorkspaceNotFound: ``workspace_id`` is not one of the caller's.
            ValueError: ``max_tiles`` is below 1.
        """
        return self._service.generate(self._source(datasource_id, user_context), user_context=user_context,
                                      title=title, workspace_id=workspace_id, max_tiles=max_tiles, on_tile=on_tile,
                                      token=cancellation_token)

    def suggest_questions(self, datasource_id: str, *, user_context: UserContext, n: int = 5) -> List[str]:
        """The questions of the first ``n`` charts ``generate`` would choose, without running them (nothing is
        saved and no model is called). An empty list when the caller reaches the datasource but no table in it.

        Raises:
            NotIndexed: the datasource has no index, or the caller cannot reach it; the message names the command.
            ValueError: ``n`` is below 1.
        """
        return self._service.suggest_questions(self._source(datasource_id, user_context), n=n)

    def _source(self, datasource_id: str, user_context: UserContext) -> Source:
        """What the starter reads, as the caller may see it: the live snapshot, the merged glossary, the caller's scope."""
        if user_context is None:
            raise TypeError("user_context is required")
        ctx = self._ctx
        scope = ctx.policy_engine.scope(user_context, datasource_id)
        # A datasource the caller cannot reach answers as one that was never indexed, so the answer says nothing
        # about it and its snapshot is not read.
        snapshot = ctx.vector_store.snapshot(datasource_id) if scope.reaches_datasource() else None
        if snapshot is None:
            raise NotIndexed(f"'{datasource_id}' has no index yet. Index it first: nl2sql index --datasource {datasource_id}")
        try:
            dialect: Optional[str] = ctx.ds_registry.get_dialect(datasource_id)
        except ValueError:  # unavailable: the types are read without a dialect
            dialect = None
        return Source(datasource_id, snapshot, dialect, scope.allows,
                      ctx.knowledge.get(datasource_id), ctx.config.settings.profiler.enum_max_distinct,
                      ctx.settings.charts.max_categories)

