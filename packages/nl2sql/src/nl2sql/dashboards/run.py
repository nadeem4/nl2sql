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
from nl2sql.common.errors import ErrorCode
from nl2sql.dashboards.models import ChartPlan, Dashboard, DashboardRun, Tile, TileRun, TileStatus
from nl2sql.dashboards.store import DashboardStore
from nl2sql.events.catalog import DashboardRefreshed
from nl2sql.hooks.approval import Approval
from nl2sql.limits import describe
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse
from nl2sql.pipeline.pinned import SuppliedPlan
from nl2sql.policies import UserContext

# What an error code says about a chart: the caller may not read it, or the stored plan no longer validates.
_DENIED = {ErrorCode.SECURITY_VIOLATION.value, ErrorCode.HOOK_DENIED.value}
_VALIDATOR = "logical_validator"
# Results a refresh does not store: the chart keeps its last one.
KEEPS_LAST = ("needs_approval", "stopped")


class DashboardBusy(ValueError):
    """The dashboard is being refreshed (or edited) by another caller or process."""


def lock_name(dashboard_id: str) -> str:
    return f"dashboard-refresh:{dashboard_id}"


class TileRunner:
    """Runs one chart: its stored plan as the caller, and the result as a ``TileRun``."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._queries = QueryAPI(ctx)
        self._results = ResultsAPI(ctx)

    def run(self, tile: Tile, user: UserContext, *, execute: bool = True, approvals: Sequence[Approval] = (),
            draw: bool = True) -> TileRun:
        """The chart's run. ``execute=False`` is the access check: the plan is validated and generated, not run.
        ``draw=False`` leaves out the chart (a check's result is thrown away)."""
        at = datetime.now(timezone.utc)
        try:
            supplied = self._supplied(tile.plan)
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
                    chart = self._draw(tile, result)
                except Exception as exc:  # one chart that cannot be drawn never stops the others
                    status, error = "error", {"code": ErrorCode.UNKNOWN_ERROR.value,
                                              "message": f"The chart could not be drawn: {exc}"}
        return TileRun(tile_id=tile.id, at=at, status=status, trace_id=result.trace_id or None, chart=chart,
                       explanation=_why(result) if status in ("ok", "empty") else None, column_kinds=kinds,
                       error=error, approvals_needed=list(result.approvals_needed))

    def _supplied(self, plan: ChartPlan) -> SuppliedPlan:
        if plan.verified is not None:
            return SuppliedPlan(verified=plan.verified, verified_datasource_id=self._datasource_of(plan))
        return SuppliedPlan(decomposition=DecomposerResponse.model_validate(plan.decomposition),
                            plans={sub_query: PlanModel.model_validate(body) for sub_query, body in plan.plans.items()})

    def _datasource_of(self, plan: ChartPlan) -> str:
        """The datasource a verified chart belongs to: stored with it, else the one datasource holding its id."""
        if plan.verified_datasource_id:
            return plan.verified_datasource_id
        curated = getattr(self._ctx, "curated", None)
        holders = [ds_id for ds_id, section in (curated.datasources.items() if curated else [])
                   if any(query.id == plan.verified.id for query in section.verified_queries)]
        if len(holders) != 1:
            raise ValueError(f"The verified query '{plan.verified.id}' is "
                             f"{'in more than one datasource' if holders else 'not in the curated answers'}.")
        return holders[0]

    def _draw(self, tile: Tile, result: QueryResult) -> ChartSpec:
        node_id = result.result_manifest[0].node_id
        try:
            spec = self._results.chart(result, node_id, kind=None if tile.chart == "auto" else tile.chart)
        except ValueError:  # the data can no longer be drawn as the chart the person forced
            if tile.chart == "auto":
                raise
            spec = self._results.chart(result, node_id)
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
    """A chart's status and its error from the run's result."""
    if result.status == "needs_approval":
        return "needs_approval", None
    if result.status == "stopped":
        return "stopped", ({"code": ErrorCode.RUN_LIMIT_REACHED.value, "message": describe(result.stop)}
                           if result.stop else None)
    if result.status in ("success", "plan_only") and not result.errors:
        rows = [summary.row_count for summary in result.result_manifest]
        return ("empty" if rows and not any(rows) else "ok"), None
    first = result.errors[0] if result.errors else {}
    if any(e.get("error_code") in _DENIED for e in result.errors):
        denied = next(e for e in result.errors if e.get("error_code") in _DENIED)
        return "denied", {"code": denied["error_code"], "message": denied.get("message", "")}
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
        """The access check of each chart under the caller's current scope: a run that does not execute a
        generated plan (a verified query is run, where the matcher is). Nothing is stored."""
        return {tile.id: self._runner.run(tile, user, execute=tile.plan.verified is not None, draw=False)
                for tile in tiles}

    def run(self, dashboard: Dashboard, tiles: Sequence[Tile], user: UserContext, trigger: str,
            approvals: Sequence[Approval] = ()) -> DashboardRun:
        """Refresh ``tiles`` in layout order as ``user``; call it under the lock. A chart that needs approval keeps
        its last result, and a chart stopped at a cap ends the refresh: the rest keep theirs too."""
        runs: List[TileRun] = []
        stopped = False
        for tile in tiles:
            if stopped:
                run = TileRun(tile_id=tile.id, at=datetime.now(timezone.utc), status="stopped")
            else:
                run = self._runner.run(tile, user, approvals=approvals)
                stopped = run.status == "stopped"
            runs.append(run)
        self._store.put_runs(dashboard.id, [r for r in runs if r.status not in KEEPS_LAST])
        counts = Counter(run.status for run in runs)
        self._ctx.bus.publish("dashboard.refreshed", DashboardRefreshed(
            dashboard_id=dashboard.id, trigger=trigger, counts=dict(counts), filters=[]),
            tenant_id=dashboard.tenant_id or self._ctx.tenant_id)
        return DashboardRun(dashboard_id=dashboard.id, at=datetime.now(timezone.utc), trigger=trigger, tiles=runs)
