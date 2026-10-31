"""Running a dashboard's charts: each stored plan is re-run through ``run_query`` (docs/architecture/wave_c.md,
section 6, D116 and D132).

A refresh costs no tokens. A chart is a run of its stored plan, ``run_query(question, plan=SuppliedPlan(...),
answer=False, user_context=...)``: the plan goes through ``plan.after``, the validator and the generator under the
caller's scope, is never cached, and is never sent to the refiner. This module imports nothing that can call a model
(held by ``test_charts_diagrams_tools_and_dashboards_make_no_model_call``) and reaches data only through
``run_query`` and ``engine.results.chart`` (``test_every_entry_point_reaches_data_through_run_query``).
"""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from nl2sql.api.query_api import QueryAPI, QueryResult
from nl2sql.api.results_api import ResultsAPI
from nl2sql.charts.spec import ChartSpec, ColumnKind
from nl2sql.common.errors import ErrorCode, NL2SQLError
from nl2sql.dashboards.filters import Active, plain, supply, values_plan
from nl2sql.dashboards.models import Dashboard, DashboardFilter, DashboardRun, Tile, TileRun, TileStatus
from nl2sql.dashboards.store import DashboardStore
from nl2sql.events.catalog import DashboardRefreshed
from nl2sql.hooks.approval import Approval
from nl2sql.limits import describe
from nl2sql.policies import UserContext

# What an error code says about a chart: the caller may not read it, or the stored plan no longer validates.
_DENIED = {ErrorCode.SECURITY_VIOLATION.value, ErrorCode.HOOK_DENIED.value}
_VALIDATOR = "logical_validator"
# Results a refresh does not store: the chart keeps its last one (a failed run, such as a database outage, never
# wipes good rows; the error is in the refresh's result).
KEEPS_LAST = ("needs_approval", "stopped", "error")
# A check that ends in one of these lets ``open`` serve the chart's stored rows; anything else fails closed.
SERVES = ("ok", "empty", "needs_approval")


class DashboardBusy(NL2SQLError, ValueError):
    """The dashboard is being refreshed (or edited) by another caller or process."""

    code, status = "CONFLICT", 409


def lock_name(dashboard_id: str) -> str:
    return f"dashboard-refresh:{dashboard_id}"


class TileRunner:
    """Runs one chart: its stored plan as the caller, and the result as a ``TileRun``."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._queries = QueryAPI(ctx)
        self._results = ResultsAPI(ctx)

    def run(self, tile: Tile, user: UserContext, *, execute: bool = True, approvals: Sequence[Approval] = (),
            draw: bool = True, active: Sequence[Active] = ()) -> TileRun:
        """The chart's run. ``execute=False`` is the access check: the plan is validated and generated, not run.
        ``draw=False`` leaves out the chart (a check's result is thrown away). ``active`` are the filters (D117)
        added to the plan as predicates before it goes to ``run_query``."""
        at = datetime.now(timezone.utc)
        try:
            supplied, every_filter = supply(tile.plan, active)
            result = self._queries.run_query(tile.question, plan=supplied, answer=False, execute=execute,
                                             user_context=user, approvals=list(approvals) or None)
        except ValueError as error:  # the stored plan does not parse, or run_query refuses it at the call
            return _needs_fixing(tile, at, str(error))
        status, error = _classify(result)
        chart: Optional[ChartSpec] = None
        kinds: Dict[str, ColumnKind] = {}
        if status in ("ok", "empty") and result.status == "success":
            kinds = dict(result.result_manifest[0].column_kinds) if result.result_manifest else {}
            if status == "ok" and draw:
                try:
                    chart = self._draw(tile, result, user)
                except Exception as exc:  # one chart that cannot be drawn never stops the others
                    status, error = "error", {"code": ErrorCode.UNKNOWN_ERROR.value,
                                              "message": f"The chart could not be drawn: {exc}"}
        return TileRun(tile_id=tile.id, at=at, status=status, trace_id=result.trace_id or None, chart=chart,
                       explanation=_why(result) if status in ("ok", "empty") else None, column_kinds=kinds,
                       filtered=every_filter, error=error, approvals_needed=list(result.approvals_needed))

    def values(self, f: DashboardFilter, user: UserContext, limit: int) -> List[Any]:
        """A filter's values (``filter_values``): a plan built in code, run as the caller through ``run_query`` with
        no model call. Raises ``ValueError`` when the run is not ``ok`` (denied, invalid, failed)."""
        result = self._queries.run_query(f"The values of the {f.label} filter", plan=values_plan(f, limit),
                                         answer=False, execute=True, user_context=user)
        status, error = _classify(result)
        if status not in ("ok", "empty"):
            raise ValueError(f"The values of filter '{f.id}' ({f.label}) could not be read: "
                             f"{(error or {}).get('message') or status}")
        if status == "empty":
            return []
        page = self._results.page(result.trace_id, result.result_manifest[0].node_id,
                                  limit=limit if f.op == "in" else 1, user_context=user)
        rows = [[plain(v) for v in row] for row in page.rows]
        if f.op == "between":
            return rows[0] if rows and any(v is not None for v in rows[0]) else []
        return [row[0] for row in rows if row and row[0] is not None]

    def _draw(self, tile: Tile, result: QueryResult, user: UserContext) -> ChartSpec:
        """The chart of the run ``user`` just asked: its results are readable by that caller only."""
        node_id = result.result_manifest[0].node_id
        try:
            spec = self._results.chart(result, node_id, user_context=user,
                                       kind=None if tile.chart == "auto" else tile.chart)
        except ValueError:  # the data can no longer be drawn as the chart the person forced
            if tile.chart == "auto":
                raise
            spec = self._results.chart(result, node_id, user_context=user)
        return spec.model_copy(update={"title": tile.title})


def _needs_fixing(tile: Tile, at: datetime, message: str) -> TileRun:
    return TileRun(tile_id=tile.id, at=at, status="needs_fixing",
                   error={"code": ErrorCode.TILE_NEEDS_FIXING.value, "message": message})


def _why(result: QueryResult) -> Optional[str]:
    if result.explanation is None:
        return None
    parts = [step.text for step in result.explanation.steps]
    if result.explanation.combined:
        parts.append(result.explanation.combined)
    return "\n".join(parts) or None


def _classify(result: QueryResult) -> Tuple[TileStatus, Optional[Dict[str, Any]]]:
    """A chart's status and its error from the run's result. A denial comes first, whatever else the run says: a
    sub-query refused while another waits for approval, or one a hook dropped while the rest ran, is ``denied``."""
    denied = next((e for e in result.errors if e.get("error_code") in _DENIED), None)
    if denied is not None:
        return "denied", {"code": denied["error_code"], "message": denied.get("message", "")}
    dropped = next((w for w in result.warnings if w.code == ErrorCode.HOOK_DENIED.value
                    and not w.detail.get("recovered")), None)
    if dropped is not None:
        return "denied", {"code": dropped.code, "message": dropped.message}
    others = [e for e in result.errors if e.get("error_code") != ErrorCode.NEEDS_APPROVAL.value]
    if result.status == "needs_approval" and not others:
        return "needs_approval", None
    if result.status == "stopped":
        return "stopped", ({"code": ErrorCode.RUN_LIMIT_REACHED.value, "message": describe(result.stop)}
                           if result.stop else None)
    if result.status in ("success", "plan_only") and not result.errors:
        rows = [summary.row_count for summary in result.result_manifest]
        return ("empty" if rows and not any(rows) else "ok"), None
    first = others[0] if others else (result.errors[0] if result.errors else {})
    invalid = next((e for e in result.errors if e.get("node") == _VALIDATOR), None)
    if invalid is not None:
        return "needs_fixing", {"code": ErrorCode.TILE_NEEDS_FIXING.value, "message": invalid.get("message", "")}
    return "error", {"code": first.get("error_code") or ErrorCode.UNKNOWN_ERROR.value,
                     "message": first.get("message") or f"The run ended {result.status or 'with no result'}."}


class Refresher:
    """Runs a dashboard's charts, one after another, stores their results and says so (D132)."""

    def __init__(self, ctx: Any, store: DashboardStore) -> None:
        self._ctx = ctx
        self._store = store
        self._runner = TileRunner(ctx)

    @contextmanager
    def locked(self, dashboard_id: str, charts: int = 1) -> Iterator[None]:
        """The dashboard's refresh lock, for a refresh or an edit. Raises ``DashboardBusy`` when it is held."""
        ttl = int(self._ctx.settings.pipeline.timeout_s * max(charts, 1)) + 10
        with ExitStack() as stack:
            try:
                stack.enter_context(self._ctx.state.lock(lock_name(dashboard_id), ttl))
            except RuntimeError as error:
                raise DashboardBusy(f"Dashboard '{dashboard_id}' is being refreshed or edited. {error}") from error
            yield

    def check(self, tiles: Sequence[Tile], user: UserContext) -> Dict[str, TileRun]:
        """The access check of each chart under the caller's current scope: a run that executes nothing (D135). A
        generated plan is validated and generated; a verified query is checked by the curated matcher. Nothing is
        stored."""
        return {tile.id: self._runner.run(tile, user, execute=False, draw=False) for tile in tiles}

    def run(self, dashboard: Dashboard, tiles: Sequence[Tile], user: UserContext, trigger: str,
            approvals: Sequence[Approval] = (), announce: bool = True, active: Sequence[Active] = ()) -> DashboardRun:
        """Refresh ``tiles`` in layout order as ``user``; call it under the lock. A chart that needs approval or
        fails keeps its last result, and a chart stopped at a cap ends the refresh: the rest keep theirs too.
        A run with ``active`` filters (D117) is a view for whoever asked: it is returned and never stored, so the
        stored results stay the dashboard's own."""
        runs: List[TileRun] = []
        stopped = False
        for tile in tiles:
            if stopped:
                run = TileRun(tile_id=tile.id, at=datetime.now(timezone.utc), status="stopped")
            else:
                run = self._runner.run(tile, user, approvals=approvals, active=active)
                stopped = run.status == "stopped"
            runs.append(run)
        if not active:
            self._store.put_runs(dashboard.id, [r for r in runs if r.status not in KEEPS_LAST])
        done = DashboardRun(dashboard_id=dashboard.id, at=datetime.now(timezone.utc), trigger=trigger, tiles=runs,
                            filters={a.filter.id: a.values for a in active})
        if announce:
            self.announce(dashboard, done)
        return done

    def announce(self, dashboard: Dashboard, done: DashboardRun) -> None:
        """Publish ``dashboard.refreshed`` for ``done``, with the ids of the filters it applied.
        ``run(..., announce=False)`` leaves it to the caller, who may drop the dashboard before it is ever shown."""
        self._ctx.bus.publish("dashboard.refreshed", DashboardRefreshed(
            dashboard_id=dashboard.id, trigger=done.trigger, counts=dict(Counter(r.status for r in done.tiles)),
            filters=list(done.filters)), tenant_id=dashboard.tenant_id or self._ctx.tenant_id)

    def values(self, f: DashboardFilter, user: UserContext, limit: int) -> List[Any]:
        return self._runner.values(f, user, limit)
