"""Manual dashboard edits (docs/architecture/wave_c.md, section 6, D133).

``apply_edits`` is pure: it works on a copy of the dashboard and returns it with what changed, so an edit list
is applied all or none (a refused edit leaves nothing behind) and the caller stores the result once. No edit
calls a model, and none takes a question or a plan: ``add_tile`` and ``replace_plan`` change *what data* a chart
shows, which only chat does (D4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Set

from nl2sql.charts.choose import choose
from nl2sql.charts.spec import ChartSpec, ColumnKind
from nl2sql.common.errors import ScheduleTooFrequent, WorkspaceNotFound
from nl2sql.common.planned import arrives_with
from nl2sql.config.models import ChartSettings
from nl2sql.dashboards.models import (
    Arrange, Dashboard, DashboardEdit, MoveDashboard, RemoveTile, RenameDashboard, RenameTile, Schedule,
    ScheduleIdentity, SetFilters, SetSchedule, SetTileChart, Tile, TileLayout, TileRun,
)


@dataclass
class EditEnv:
    """What an edit needs to know beyond the dashboard."""

    identity: ScheduleIdentity  # the caller, saved on a schedule it sets (D134)
    now: datetime
    min_refresh_minutes: int
    workspace_ids: Set[str]  # the caller's workspaces
    chart_settings: ChartSettings
    last_run: Callable[[str], Optional[TileRun]]  # a tile's last stored result


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
        handler = _HANDLERS.get(edit.kind)
        if handler is None:
            raise arrives_with(f"The '{edit.kind}' edit", "D4")
        applied.dashboard = handler(applied.dashboard, edit, env, applied)
        if edit.kind not in applied.kinds:
            applied.kinds.append(edit.kind)
    return applied


def _title(text: str, what: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError(f"A {what} title cannot be empty.")
    return text


def _tile(dashboard: Dashboard, tile_id: str) -> Tile:
    for tile in dashboard.tiles:
        if tile.id == tile_id:
            return tile
    raise ValueError(f"No chart '{tile_id}' in this dashboard.")


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
            raise ValueError(f"Chart '{item.tile_id}' is arranged more than once.")
        places[item.tile_id] = TileLayout(x=item.x, y=item.y, w=item.w, h=item.h)
    tiles = [t.model_copy(update={"layout": places[t.id]}) if t.id in places else t for t in d.tiles]
    tiles.sort(key=lambda t: (t.layout.y, t.layout.x))  # layout order: the order charts run and read in
    return d.model_copy(update={"tiles": tiles})


def _set_filters(d: Dashboard, e: SetFilters, env: EditEnv, out: Applied) -> Dashboard:
    seen: Set[str] = set()
    for f in e.filters:
        if f.id in seen:
            raise ValueError(f"Filter id '{f.id}' is used twice.")
        seen.add(f.id)
        if len(f.column.split(".")) != 4 or not all(f.column.split(".")):
            raise ValueError(f"Filter '{f.id}': the column must be 'datasource.schema.table.column', not '{f.column}'.")
        if f.op == "between" and f.kind not in ("number", "temporal"):
            raise ValueError(f"Filter '{f.id}': a 'between' filter needs a number or temporal column, not {f.kind}.")
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


_HANDLERS = {"rename": _rename, "rename_tile": _rename_tile, "set_tile_chart": _set_tile_chart,
             "remove_tile": _remove_tile, "arrange": _arrange, "set_filters": _set_filters,
             "set_schedule": _set_schedule, "move": _move}
