"""Manual dashboard edits (docs/architecture/wave_c.md, section 6, D133).

``apply_edits`` is pure: it works on a copy of the dashboard and returns it with what changed, so an edit list
is applied all or none (a refused edit leaves nothing behind) and the caller stores the result once. No edit
calls a model, and none takes a question or a plan: ``add_tile`` and ``replace_plan`` change *what data* a chart
shows, which only chat does (D4): they read the plan from an `ask` step of a conversation and check it with a
no-execute run before it is saved, so a plan the validator would reject never lands in a dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from nl2sql.charts.choose import choose
from nl2sql.charts.spec import ChartSpec, ColumnKind
from nl2sql.common.errors import InvalidArgument, ScheduleTooFrequent, WorkspaceNotFound
from nl2sql.config.models import ChartSettings
from nl2sql.conversations.models import Conversation
from nl2sql.dashboards.models import (
    GRID_COLUMNS, AddTile, Arrange, ChartPlan, Dashboard, DashboardEdit, MoveDashboard, RemoveTile, RenameDashboard,
    RenameTile, ReplacePlan, Schedule, ScheduleIdentity, SetFilters, SetSchedule, SetTileChart, StepRef, Tile,
    TileLayout, TileRun,
)
from nl2sql.hooks.approval import VerifiedRef


@dataclass
class EditEnv:
    """What an edit needs to know beyond the dashboard."""

    identity: ScheduleIdentity  # the caller, saved on a schedule it sets (D134)
    now: datetime
    min_refresh_minutes: int
    workspace_ids: Set[str]  # the caller's workspaces
    chart_settings: ChartSettings
    last_run: Callable[[str], Optional[TileRun]]  # a tile's last stored result
    # Chat only (D4): the question and plan of an ``ask`` step, and the no-execute check of a chart, which raises
    # ``ValueError`` when its plan is refused. Absent, ``add_tile`` and ``replace_plan`` are refused.
    step: Optional[Callable[[StepRef], Tuple[str, ChartPlan]]] = None
    check: Optional[Callable[[Tile], None]] = None


@dataclass
class Applied:
    dashboard: Dashboard
    kinds: List[str] = field(default_factory=list)  # the edit kinds, in order, once each
    dropped: Set[str] = field(default_factory=set)  # tiles whose stored result no longer holds
    schedule_set: bool = False  # a set_schedule edit was applied


def apply_edits(dashboard: Dashboard, edits: Sequence[DashboardEdit], env: EditEnv) -> Applied:
    """``dashboard`` with ``edits`` applied in order, each seeing the ones before it. Raises on the first edit
    that cannot be applied (``ValueError``, ``WorkspaceNotFound``, ``ScheduleTooFrequent``)."""
    applied = Applied(dashboard=dashboard)
    for edit in edits:
        applied.dashboard = _HANDLERS[edit.kind](applied.dashboard, edit, env, applied)
        if edit.kind not in applied.kinds:
            applied.kinds.append(edit.kind)
    return applied


def _title(text: str, what: str) -> str:
    text = text.strip()
    if not text:
        raise InvalidArgument(f"A {what} title cannot be empty.")
    return text


def _tile(dashboard: Dashboard, tile_id: str) -> Tile:
    for tile in dashboard.tiles:
        if tile.id == tile_id:
            return tile
    raise InvalidArgument(f"No chart '{tile_id}' in this dashboard.")


def _with_tile(dashboard: Dashboard, tile_id: str, **changes) -> Dashboard:
    _tile(dashboard, tile_id)
    tiles = [t.model_copy(update=changes) if t.id == tile_id else t for t in dashboard.tiles]
    return dashboard.model_copy(update={"tiles": tiles})


def _rename(d: Dashboard, e: RenameDashboard, env: EditEnv, out: Applied) -> Dashboard:
    return d.model_copy(update={"title": _title(e.title, "dashboard")})


def _rename_tile(d: Dashboard, e: RenameTile, env: EditEnv, out: Applied) -> Dashboard:
    return _with_tile(d, e.tile_id, title=_title(e.title, "chart"))


def _kinds_of(spec: ChartSpec) -> Dict[str, ColumnKind]:
    kinds: Dict[str, ColumnKind] = {column: "other" for column in spec.data.columns}
    for encoding in (spec.x, spec.series, spec.value, *spec.y):
        if encoding is not None:
            kinds[encoding.field] = encoding.kind
    return kinds


def _set_tile_chart(d: Dashboard, e: SetTileChart, env: EditEnv, out: Applied) -> Dashboard:
    _tile(d, e.tile_id)
    run = env.last_run(e.tile_id)
    spec = run.chart if run is not None else None
    if spec is not None and e.chart != "auto":
        data = spec.data
        # The kinds stored with the result; a row without them falls back to what the chart's encodings carry.
        kinds = {column: run.column_kinds.get(column, "other") for column in data.columns} \
            if run.column_kinds else _kinds_of(spec)
        # Raises ValueError naming the kinds the stored result can be drawn as.
        choose(columns=data.columns, kinds=kinds, rows=data.rows, row_count=len(data.rows),
               title=spec.title, trace_id=data.trace_id, result_id=data.result_id, settings=env.chart_settings,
               kind=e.chart, cut=data.truncated)
    return _with_tile(d, e.tile_id, chart=e.chart)


def _remove_tile(d: Dashboard, e: RemoveTile, env: EditEnv, out: Applied) -> Dashboard:
    _tile(d, e.tile_id)
    out.dropped.add(e.tile_id)
    return d.model_copy(update={"tiles": [t for t in d.tiles if t.id != e.tile_id]})


def _arrange(d: Dashboard, e: Arrange, env: EditEnv, out: Applied) -> Dashboard:
    places: Dict[str, TileLayout] = {}
    for item in e.layouts:
        _tile(d, item.tile_id)
        if item.tile_id in places:
            raise InvalidArgument(f"Chart '{item.tile_id}' is arranged more than once.")
        places[item.tile_id] = TileLayout(x=item.x, y=item.y, w=item.w, h=item.h)
    tiles = [t.model_copy(update={"layout": places[t.id]}) if t.id in places else t for t in d.tiles]
    tiles.sort(key=lambda t: (t.layout.y, t.layout.x))  # layout order: the order charts run and read in
    return d.model_copy(update={"tiles": tiles})


def _set_filters(d: Dashboard, e: SetFilters, env: EditEnv, out: Applied) -> Dashboard:
    seen: Set[str] = set()
    for f in e.filters:
        if f.id in seen:
            raise InvalidArgument(f"Filter id '{f.id}' is used twice.")
        seen.add(f.id)
        if len(f.column.split(".")) != 4 or not all(f.column.split(".")):
            raise InvalidArgument(f"Filter '{f.id}': the column must be 'datasource.schema.table.column', not '{f.column}'.")
        if f.op == "between" and f.kind not in ("number", "temporal"):
            raise InvalidArgument(f"Filter '{f.id}': a 'between' filter needs a number or temporal column, not {f.kind}.")
    if e.filters != d.filters:  # a filter changes what every chart shows
        out.dropped.update(t.id for t in d.tiles)
    return d.model_copy(update={"filters": list(e.filters)})


def _set_schedule(d: Dashboard, e: SetSchedule, env: EditEnv, out: Applied) -> Dashboard:
    out.schedule_set = True
    if e.every_minutes is None:
        return d.model_copy(update={"schedule": None})
    if e.every_minutes < env.min_refresh_minutes:
        raise ScheduleTooFrequent(f"A schedule can run at most every {env.min_refresh_minutes} minutes "
                                  f"(dashboards.min_refresh_minutes); {e.every_minutes} is too often.")
    schedule = Schedule(every_minutes=e.every_minutes, identity=env.identity, set_at=env.now,
                        next_at=env.now + timedelta(minutes=e.every_minutes), state="active")
    return d.model_copy(update={"schedule": schedule})


def _move(d: Dashboard, e: MoveDashboard, env: EditEnv, out: Applied) -> Dashboard:
    if e.workspace_id not in env.workspace_ids:
        raise WorkspaceNotFound(f"No workspace '{e.workspace_id}' for this caller.")
    return d.model_copy(update={"workspace_id": e.workspace_id})


def plan_from_step(conversation: Conversation, ref: StepRef) -> Tuple[str, ChartPlan]:
    """The question and the whole plan of an ``ask`` step the conversation holds: its decomposition with the plan of
    each sub-query, or the verified query that answered. ``ref.step`` counts the turn's ``ask`` steps from 1, as the
    ``chart`` tool does. Nothing but what the engine stored is read: no model text becomes a plan (D4)."""
    if ref.turn is None:
        turn = conversation.turns[-1] if conversation.turns else None
    else:
        turn = next((t for t in conversation.turns if t.index == ref.turn), None)
    if turn is None:
        raise InvalidArgument(f"There is no turn {ref.turn or 'yet'} in this conversation.")
    asks = [s for s in turn.steps if s.call.name == "ask" and s.result is not None]  # as the chart tool counts
    if ref.step > len(asks):
        raise InvalidArgument(f"Turn {ref.turn} has {len(asks)} answer(s) to build a chart from; there is no step {ref.step}.")
    step = asks[ref.step - 1]
    result = step.result
    if step.status != "ok" or result is None:
        raise InvalidArgument(f"Step {ref.step} of turn {ref.turn} did not answer ({step.status}); a chart needs an answer.")
    if result.source.type == "verified":
        plan = ChartPlan(verified=VerifiedRef(id=result.source.id or "", params=dict(result.source.params or {})),
                         verified_datasource_id=result.source.datasource_id)
    else:
        plans = {sq.id: sq.plan for sq in result.sub_queries if sq.plan}
        missing = [] if result.decomposition is None else [
            s.id for s in result.decomposition.sub_queries if s.id not in plans]
        if result.decomposition is None or missing:
            raise InvalidArgument(f"Step {ref.step} of turn {ref.turn} kept no plan to build a chart from.")
        plan = ChartPlan(decomposition=result.decomposition.model_dump(mode="json"),
                         plans={s.id: plans[s.id] for s in result.decomposition.sub_queries})
    return step.question or "", plan


def _from_step(env: EditEnv, kind: str, ref: StepRef) -> Tuple[str, ChartPlan]:
    if env.step is None or env.check is None:
        raise InvalidArgument(f"The '{kind}' edit exists only in a conversation: it takes the plan from an answer the "
                         "conversation holds (the dashboard_edit chat tool).")
    return env.step(ref)


def _free_id(tiles: Sequence[Tile]) -> str:
    taken = {t.id for t in tiles}
    n = 1
    while f"t{n}" in taken:
        n += 1
    return f"t{n}"


def _next_layout(tiles: Sequence[Tile]) -> TileLayout:
    """Beside the last chart when there is room on its row, else a new row."""
    if not tiles:
        return TileLayout(x=0, y=0, w=2, h=2)
    last = max(tiles, key=lambda t: (t.layout.y, t.layout.x))
    if last.layout.x + last.layout.w + 2 <= GRID_COLUMNS:
        return TileLayout(x=last.layout.x + last.layout.w, y=last.layout.y, w=2, h=2)
    return TileLayout(x=0, y=max(t.layout.y + t.layout.h for t in tiles), w=2, h=2)


def _add_tile(d: Dashboard, e: AddTile, env: EditEnv, out: Applied) -> Dashboard:
    question, plan = _from_step(env, e.kind, e.from_step)
    tile = Tile(id=_free_id(d.tiles), title=_title(e.title, "chart"), question=question, plan=plan,
                layout=_next_layout(d.tiles))
    env.check(tile)
    return d.model_copy(update={"tiles": [*d.tiles, tile]})


def _replace_plan(d: Dashboard, e: ReplacePlan, env: EditEnv, out: Applied) -> Dashboard:
    old = _tile(d, e.tile_id)
    question, plan = _from_step(env, e.kind, e.from_step)
    env.check(old.model_copy(update={"question": question, "plan": plan}))
    out.dropped.add(e.tile_id)  # the stored result belonged to the old plan
    return _with_tile(d, e.tile_id, question=question, plan=plan)


_HANDLERS = {"rename": _rename, "rename_tile": _rename_tile, "set_tile_chart": _set_tile_chart,
             "remove_tile": _remove_tile, "arrange": _arrange, "set_filters": _set_filters,
             "set_schedule": _set_schedule, "move": _move, "add_tile": _add_tile, "replace_plan": _replace_plan}
