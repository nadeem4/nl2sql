"""The four chart templates of the starter dashboard (docs/architecture/wave_c.md, section 6, D115).

Each template builds a ``SubQuery`` and a ``PlanModel`` in code, with its own question text from the columns' names
humanised, and stores them as an ordinary ``ChartPlan``. The plan then takes the same path as a model's: ``plan.after``,
the validator, the generator, ``execute.before``, the adapter. Nothing here names a dialect or writes SQL, and no model
is asked: a template is data and a few rules.

| Group | Question | Chart | At most |
|---|---|---|---|
| Key totals | "How many rows are in {table}?"; "What is the total {measure}?" | big number | 4 |
| Trends | "What is the total {measure} by month over the last 12 months of data?" | line | 2 |
| Top N | "Which 10 {dimension} have the highest total {measure}?" | bar | 2 |
| Distributions | "How many {table} rows are there per {dimension}?" | bar | 2 |
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from nl2sql.dashboards.models import ChartPlan, DashboardFilter
from nl2sql.dashboards.roles import Col, Table
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, GroupByItem, OrderItem, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery

MAX_TOTALS = 4  # the row count and up to three measures
MAX_TRENDS = MAX_TOPS = MAX_DISTRIBUTIONS = 2
TOP_N = 10
MAX_DISTRIBUTION_VALUES = 12
TREND_MONTHS = 12

KINDS = {"totals": "big_number", "trend": "line", "top": "bar", "distribution": "bar"}  # the chart each should draw

_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


@dataclass(frozen=True)
class Candidate:
    """One chart the starter may keep. ``kind`` is the chart it should draw as (the provisional layout); a filter
    follows ``time`` and ``dimension`` when the chart is kept."""

    title: str
    question: str
    plan: ChartPlan
    kind: str
    datasource_id: str
    group: str  # totals, trend, top or distribution
    time: Optional[Tuple[Table, Col]] = None
    dimension: Optional[Tuple[Table, Col]] = None


def filters_for(kept: List[Candidate]) -> List[DashboardFilter]:
    """A ``between`` filter on the time column the kept trends use, and an ``in`` filter on each of at most two
    dimensions the kept distributions use."""
    found: List[Tuple[Table, Col, str]] = []
    for candidate in kept:
        if candidate.group == "trend" and not any(op == "between" for _, _, op in found):
            found.append((*candidate.time, "between"))
        elif candidate.group == "distribution" and len([f for f in found if f[2] == "in"]) < MAX_DISTRIBUTIONS:
            found.append((*candidate.dimension, "in"))
    filters, seen = [], set()
    for table, column, op in found:
        ref = table.contract.table
        full = f"{kept[0].datasource_id}.{ref.schema_name}.{ref.table_name}.{column.name}"
        if full not in seen:
            seen.add(full)
            filters.append(DashboardFilter(
                id=f"{_slug(table.name)}_{_slug(column.name)}", label=_cap(_words(column.name)), column=full, op=op,
                kind="temporal" if op == "between" else (column.kind if column.kind != "other" else "text")))
    return filters


def candidates(datasource_id: str, tables: List[Table]) -> List[Candidate]:
    """Every chart the ranked ``tables`` suggest, in the order they are kept: key totals, trends, top N, distributions.
    The same tables always give the same list."""
    if not tables:
        return []
    out: List[Candidate] = []
    measures = [(t, m) for t in tables for m in t.measures]
    out.append(_total(datasource_id, tables[0], None))
    out += [_total(datasource_id, t, m) for t, m in measures[:MAX_TOTALS - 1]]
    out += [_trend(datasource_id, t, t.times[0], m) for t in tables if t.times for m in t.measures][:MAX_TRENDS]
    out += [_top(datasource_id, t, d, m) for t in tables for m in t.measures for d in t.dimensions][:MAX_TOPS]
    out += [_distribution(datasource_id, t, d) for t in tables for d in t.dimensions
            if d.distinct <= MAX_DISTRIBUTION_VALUES][:MAX_DISTRIBUTIONS]
    return out


# ------------------------------------------------------------------ the four templates

def _total(ds: str, table: Table, measure: Optional[Col]) -> Candidate:
    if measure is None:
        question = f"How many rows are in {_words(table.name)}?"
        select = SelectItem(expr=_count(), alias="row_count")
        return _candidate("totals", ds, table, f"Number of {_words(table.name)}", question, [select])
    name = _words(measure.name)
    select = SelectItem(expr=_func("SUM", _col(measure)), alias=f"total_{_slug(measure.name)}")
    return _candidate("totals", ds, table, f"Total {name}", f"What is the total {name}?", [select])


def _trend(ds: str, table: Table, time: Col, measure: Col) -> Candidate:
    name = _words(measure.name)
    month = _func("DATE_TRUNC", _lit("month"), _col(time), aggregate=False)
    total = _func("SUM", _col(measure))
    where = None
    if (cutoff := _cutoff(time.maximum)) is not None:
        where = Expr(kind="binary", op=">=", left=_col(time), right=_lit(cutoff))
    return _candidate(
        "trend", ds, table, f"{_cap(name)} by month",
        f"What is the total {name} by month over the last {TREND_MONTHS} months of data?",
        [SelectItem(expr=month, alias="month"), SelectItem(expr=total, alias=f"total_{_slug(measure.name)}")],
        where=where, group_by=[GroupByItem(expr=month)], order_by=[OrderItem(expr=month, direction="asc")], time=time)


def _top(ds: str, table: Table, dimension: Col, measure: Col) -> Candidate:
    m, d = _words(measure.name), _words(dimension.name)
    total = _func("SUM", _col(measure))
    return _candidate(
        "top", ds, table, f"Top {TOP_N} {d} by {m}", f"Which {TOP_N} {d} have the highest total {m}?",
        [SelectItem(expr=_col(dimension), alias=dimension.name),
         SelectItem(expr=total, alias=f"total_{_slug(measure.name)}")],
        group_by=[GroupByItem(expr=_col(dimension))], order_by=[OrderItem(expr=total, direction="desc")], limit=TOP_N,
        dimension=dimension)


def _distribution(ds: str, table: Table, dimension: Col) -> Candidate:
    t, d = _words(table.name), _words(dimension.name)
    count = _count()
    return _candidate(
        "distribution", ds, table, f"{_cap(t)} by {d}", f"How many {t} rows are there per {d}?",
        [SelectItem(expr=_col(dimension), alias=dimension.name), SelectItem(expr=count, alias="row_count")],
        group_by=[GroupByItem(expr=_col(dimension))], order_by=[OrderItem(expr=count, direction="desc")],
        dimension=dimension)


# ------------------------------------------------------------------ building a stored plan

def _candidate(group: str, ds: str, table: Table, title: str, question: str, select: List[SelectItem], *,
               where: Optional[Expr] = None, group_by: Optional[List[GroupByItem]] = None,
               order_by: Optional[List[OrderItem]] = None, limit: Optional[int] = None, time: Optional[Col] = None,
               dimension: Optional[Col] = None) -> Candidate:
    ref = table.contract.table
    plan = PlanModel(tables=[TableRef(name=ref.table_name, schema_name=ref.schema_name, alias=_ALIAS)],
                     select_items=select, where=where, group_by=group_by or [], order_by=order_by or [], limit=limit)
    sub_query = SubQuery(id="sq1", datasource_id=ds, intent=question)
    decomposition = DecomposerResponse(sub_queries=[sub_query], combine_groups=[
        CombineGroup(group_id="g1", operation="standalone", inputs=[CombineInput(subquery_id="sq1")])])
    stored = ChartPlan(decomposition=decomposition.model_dump(mode="json"),
                       plans={"sq1": plan.model_dump(mode="json")})
    return Candidate(title, question, stored, KINDS[group], ds, group, time=(table, time) if time else None,
                     dimension=(table, dimension) if dimension else None)


_ALIAS = "t1"


def _col(column: Col) -> Expr:
    return Expr(kind="column", alias=_ALIAS, column_name=column.name)


def _count() -> Expr:
    """``COUNT(*)``: the rows."""
    return _func("COUNT", Expr(kind="column", column_name="*"))


def _func(name: str, *args: Expr, aggregate: bool = True) -> Expr:
    return Expr(kind="func", func_name=name, args=list(args), is_aggregate=aggregate)


def _lit(value: Any) -> Expr:
    return Expr(kind="literal", value=value)


def _cutoff(latest: Any) -> Optional[str]:
    """The ISO day ``TREND_MONTHS`` months before the snapshot's latest value, or None when it is not a date."""
    match = _ISO_DAY.match(str(latest)) if latest is not None else None
    if match is None:
        return None
    year, month, day = (int(g) for g in match.groups())
    month -= TREND_MONTHS
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}-{month:02d}-{min(day, calendar.monthrange(year, month)[1]):02d}"


def _words(name: str) -> str:
    return " ".join(w.lower() for w in _WORDS.findall(name))


def _slug(name: str) -> str:
    return _words(name).replace(" ", "_")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]
