"""Choosing a chart from a result's column kinds and, for a single-sub-query run, its plan (D111, D112).

Five rules, first match wins: a big number, a line, a line split into series, a bar, else a table. No model
call: ``choose`` reads kinds and rows only. ``docs/architecture/wave_c.md``, section 5.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from nl2sql.charts.spec import ChartData, ChartSpec, ColumnKind, Encoding
from nl2sql.charts.takeaway import takeaway
from nl2sql.config import ChartSettings
from nl2sql.pipeline.nodes.ast_planner.functions import AGGREGATE_FUNCTIONS, date_operation
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel

MAX_BAR_MEASURES = 3
_SUMS = {"SUM", "COUNT"}


def humanise(name: str) -> str:
    """``total_amount`` -> ``Total amount``."""
    text = name.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _refine(columns: List[str], kinds: Dict[str, ColumnKind],
            plan: Optional[PlanModel]) -> Tuple[Dict[str, ColumnKind], Set[str], Set[str]]:
    """The columns' kinds after the plan refines them, the measures, and the measures that are a SUM or COUNT.

    Without a plan every ``number`` is a measure. With one, a select item that is ``DATE_TRUNC`` is temporal, a
    grouped ``DATE_PART('year', x)`` is temporal (a year axis), an aggregate is a measure, and a number that is
    grouped on is a dimension.
    """
    kinds = {c: kinds.get(c, "other") for c in columns}
    measures = {c for c, k in kinds.items() if k == "number"}
    sums: Set[str] = set()
    if plan is None:
        return kinds, measures, sums
    grouped = [item.expr.model_dump() for item in plan.group_by]
    for item in plan.select_items:
        expr = item.expr
        name = item.alias or (expr.column_name if expr.kind == "column" else None)
        if name not in kinds:
            continue
        is_grouped = expr.model_dump() in grouped
        operation = date_operation(expr.func_name, expr.args) if expr.kind == "func" else None
        func = (expr.func_name or "").upper() if expr.kind == "func" else ""
        if operation and (operation[0] == "trunc" or (operation[1] == "year" and is_grouped)):
            kinds[name] = "temporal"
            measures.discard(name)
        elif expr.kind == "func" and (expr.is_aggregate or func in AGGREGATE_FUNCTIONS):
            if kinds[name] == "number":
                measures.add(name)
                if func in _SUMS:
                    sums.add(name)
        elif is_grouped:
            measures.discard(name)
    return kinds, measures, sums


def _encoding(name: str, kinds: Dict[str, ColumnKind]) -> Encoding:
    return Encoding(field=name, kind=kinds[name], title=humanise(name))


def _sorted(rows: List[List[Any]], index: int, descending: bool = False) -> List[List[Any]]:
    """``rows`` ordered by column ``index``, nulls last; unchanged when the values cannot be compared."""
    present = [r for r in rows if r[index] is not None]
    try:
        present.sort(key=lambda r: r[index], reverse=descending)
    except TypeError:
        return rows
    return present + [r for r in rows if r[index] is None]


def choose(*, columns: List[str], kinds: Dict[str, ColumnKind], rows: List[List[Any]], row_count: int,
           title: str, trace_id: str, result_id: str, settings: ChartSettings,
           plan: Optional[PlanModel] = None, kind: Optional[str] = None) -> ChartSpec:
    """The chart for ``rows`` (at most ``settings.max_points``, the first rows of a result of ``row_count``).

    ``kind`` forces one the data can draw, else ``ValueError`` names the ones it can.
    """
    kinds, measures, sums = _refine(columns, kinds, plan)
    dims = [c for c in columns if c not in measures]
    ms = [c for c in columns if c in measures]
    truncated = row_count > len(rows)
    n = len(rows)
    index = {c: i for i, c in enumerate(columns)}
    drafts: Dict[str, Dict[str, Any]] = {}

    if not truncated:
        if n == 1 and len(ms) == 1 and len(dims) <= 1 and all(kinds[d] == "text" for d in dims):
            drafts["big_number"] = {"value": _encoding(ms[0], kinds), "reason": "one row and one number"}
        if n >= 2 and len(dims) == 1 and kinds[dims[0]] == "temporal" and 1 <= len(ms) <= settings.max_series:
            drafts["line"] = {"x": _encoding(dims[0], kinds), "y": [_encoding(m, kinds) for m in ms],
                              "rows": _sorted(rows, index[dims[0]]),
                              "reason": f"one date column and {len(ms)} number{'s' if len(ms) > 1 else ''}"}
        elif n >= 2 and len(dims) == 2 and len(ms) == 1:
            time = [d for d in dims if kinds[d] == "temporal"]
            label = [d for d in dims if kinds[d] == "text"]
            if time and label and len({r[index[label[0]]] for r in rows}) <= settings.max_series:
                drafts["line"] = {"x": _encoding(time[0], kinds), "y": [_encoding(ms[0], kinds)],
                                  "series": _encoding(label[0], kinds), "rows": _sorted(rows, index[time[0]]),
                                  "reason": "one date column, one category column and one number"}
        if (2 <= n <= settings.max_categories and len(dims) == 1 and kinds[dims[0]] in ("text", "boolean")
                and 1 <= len(ms) <= MAX_BAR_MEASURES):
            ordered = rows if plan is not None and plan.order_by else _sorted(rows, index[ms[0]], descending=True)
            drafts["bar"] = {"x": _encoding(dims[0], kinds), "y": [_encoding(m, kinds) for m in ms],
                             "rows": ordered,
                             "reason": f"one category column and {len(ms)} number{'s' if len(ms) > 1 else ''}"}
    fallback = (f"the result has more than {settings.max_points} rows" if truncated
                else "the columns do not fit a single number, a line or a bar")
    drafts["table"] = {"reason": "a table was asked for" if kind == "table" else fallback}
    order = ("big_number", "line", "bar", "table")
    if kind is None:
        # The rules match disjoint data, so at most one chart kind is drafted; a table is the fallback.
        chosen = next(k for k in order if k in drafts)
    elif kind in drafts:
        chosen = kind
    else:
        drawable = ", ".join(k for k in order if k in drafts)
        raise ValueError(f"This result cannot be drawn as '{kind}'; it can be drawn as: {drawable}.")

    draft = drafts[chosen]
    spec = ChartSpec(
        kind=chosen, title=title, x=draft.get("x"), y=draft.get("y", []), series=draft.get("series"),
        value=draft.get("value"),
        data=ChartData(trace_id=trace_id, result_id=result_id, columns=columns,
                       rows=draft.get("rows", rows), truncated=truncated),
        reason=draft["reason"])
    first = spec.y[0].field if spec.y else None
    return spec.model_copy(update={"takeaway": takeaway(spec, share_of_total=first in sums)})
