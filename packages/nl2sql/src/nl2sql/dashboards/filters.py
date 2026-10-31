"""Dashboard filters (docs/architecture/wave_c.md, section 6 "Filters", D117).

A filter value becomes a typed predicate added to each stored plan that reads the filter's table, before ``plan.after``,
the validator and the generator: never text added to a question or to SQL. The values are plan literals, so the
generator renders and escapes them as it renders any literal; a filter only adds ``AND``-ed predicates, so it can never
widen what a chart reads. Everything here is pure: running the plans is ``run.py``'s job.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import reduce
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from nl2sql.common.errors import InvalidArgument, UnknownFilter
from nl2sql.dashboards.models import ChartPlan, DashboardFilter
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, OrderItem, PlanModel, SelectItem, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery
from nl2sql.pipeline.pinned import SuppliedPlan

MAX_IN_VALUES = 100
MAX_VALUES_LIMIT = 1000  # what one page of a saved result can hold


@dataclass(frozen=True)
class Active:
    """A filter with the checked values a refresh was given for it."""

    filter: DashboardFilter
    values: List[Any]

    @property
    def datasource(self) -> str:
        return self.filter.column.split(".")[0]

    @property
    def schema(self) -> str:
        return self.filter.column.split(".")[1]

    @property
    def table(self) -> str:
        return self.filter.column.split(".")[2]

    @property
    def column(self) -> str:
        return self.filter.column.split(".")[3]


def find(filters: Sequence[DashboardFilter], filter_id: str) -> DashboardFilter:
    """The dashboard's filter ``filter_id``; an ``UnknownFilter`` naming the ids it has when there is none."""
    for f in filters:
        if f.id == filter_id:
            return f
    ids = ", ".join(f"'{f.id}'" for f in filters) or "none"
    raise UnknownFilter(f"No filter '{filter_id}' in this dashboard; its filters: {ids}.")


def activate(filters: Sequence[DashboardFilter], given: Optional[Mapping[str, Any]]) -> List[Active]:
    """The filters ``given`` (id to values), checked against the dashboard's definitions and returned in the
    dashboard's order. The validator does not compare a literal with its column's type, so this does, before any
    plan is built.

    Raises:
        ValueError: an id the dashboard does not have, or a value of the wrong type or count; it names the filter.
    """
    given = given or {}
    for filter_id in given:
        find(filters, filter_id)
    return [Active(f, _checked(f, given[f.id])) for f in filters if f.id in given]


def _checked(f: DashboardFilter, values: Any) -> List[Any]:
    name = f"Filter '{f.id}' ({f.label})"
    if not isinstance(values, (list, tuple)):
        raise InvalidArgument(f"{name} takes a list of values.")
    if f.op == "between" and len(values) != 2:
        raise InvalidArgument(f"{name} takes exactly two values, the first and the last of the range.")
    if f.op == "in" and not 1 <= len(values) <= MAX_IN_VALUES:
        raise InvalidArgument(f"{name} takes 1 to {MAX_IN_VALUES} values, not {len(values)}.")
    for value in values:
        if not _fits(f.kind, value):
            raise InvalidArgument(f"{name} takes {_EXPECTED[f.kind]} values, not {value!r}.")
    return list(values)


_EXPECTED = {"number": "number", "text": "text", "boolean": "boolean (true or false)", "other": "plain",
             "temporal": "ISO date or timestamp text (such as 2024-01-31 or 2024-01-31T12:00:00Z)"}


def _fits(kind: str, value: Any) -> bool:
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "text":
        return isinstance(value, str)
    if kind == "temporal":
        return isinstance(value, str) and _iso(value)
    return isinstance(value, (str, int, float, bool)) and not (isinstance(value, float) and not math.isfinite(value))


# The only temporal forms: a date, or a date and a time with optional seconds, fraction and Z or an offset.
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})?")


def _iso(text: str) -> bool:
    try:
        if _DATE.fullmatch(text):
            date.fromisoformat(text)
        elif _STAMP.fullmatch(text):
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            return False
    except ValueError:
        return False
    return True


def _day_after(text: str) -> Optional[str]:
    """The day after a date-only ``text``, or ``None`` for a timestamp (or the last day there is)."""
    if not _DATE.fullmatch(text):
        return None
    try:
        return (date.fromisoformat(text) + timedelta(days=1)).isoformat()
    except OverflowError:
        return None


# ------------------------------------------------------------------ the predicate

def _column(alias: str, name: str) -> Expr:
    return Expr(kind="column", alias=alias, column_name=name)


def _binary(op: str, left: Expr, right: Expr) -> Expr:
    return Expr(kind="binary", op=op, left=left, right=right)


def predicate(active: Active, alias: str) -> Expr:
    """``between [a, b]`` is ``col >= a AND col <= b``; ``in [v1, v2]`` is ``col = v1 OR col = v2``. A temporal
    range whose upper bound is a date alone ends with that whole day: ``col >= a AND col < the next day``."""
    column = _column(alias, active.column)
    if active.filter.op == "between":
        low, high = active.values
        below = _day_after(high) if active.filter.kind == "temporal" else None
        upper = _binary("<", column, Expr(kind="literal", value=below)) if below else \
            _binary("<=", column, Expr(kind="literal", value=high))
        return _binary("AND", _binary(">=", column, Expr(kind="literal", value=low)), upper)
    terms = [_binary("=", column, Expr(kind="literal", value=v)) for v in active.values]
    return reduce(lambda left, right: _binary("OR", left, right), terms)


def _reads(ref: TableRef, active: Active) -> bool:
    """A table matches by name and schema, ignoring case. A plan table that names no schema never matches: it could
    be the filter's table or a same-named one in the datasource's default schema, and a wrong guess would filter the
    wrong table."""
    return (ref.name.casefold() == active.table.casefold() and bool(ref.schema_name)
            and ref.schema_name.casefold() == active.schema.casefold())


def filtered(plan: PlanModel, datasource_id: str, active: Sequence[Active]) -> Tuple[PlanModel, Set[str]]:
    """``plan`` with the predicate of each filter on its table added to its ``where``, and the ids of the filters
    that reached it. A filter of another datasource, or on a table the plan does not read, is skipped."""
    where = plan.where
    reached: Set[str] = set()
    for a in active:
        if a.datasource != datasource_id:
            continue
        for ref in (r for r in plan.tables if _reads(r, a)):
            added = predicate(a, ref.alias)
            where = added if where is None else _binary("AND", where, added)
            reached.add(a.filter.id)
    return (plan.model_copy(update={"where": where}) if reached else plan), reached


def supply(plan: ChartPlan, active: Sequence[Active]) -> Tuple[SuppliedPlan, bool]:
    """The chart's stored plan as a ``SuppliedPlan`` with every active filter added where it applies, and whether the
    chart was filtered throughout: every query got a filter and every active filter reached a query. A verified
    query has no plan, so it runs unfiltered; so does a query that reads none of the filters' tables."""
    if plan.verified is not None:
        return SuppliedPlan(verified=plan.verified, verified_datasource_id=plan.verified_datasource_id), False
    decomposition = DecomposerResponse.model_validate(plan.decomposition)
    datasources = {sq.id: sq.datasource_id for sq in decomposition.sub_queries}
    plans: Dict[str, PlanModel] = {}
    reached: Set[str] = set()
    every_query = bool(plan.plans)
    for sub_query, body in plan.plans.items():
        plans[sub_query], here = filtered(PlanModel.model_validate(body), datasources.get(sub_query, ""), active)
        reached |= here
        every_query = every_query and bool(here)
    return (SuppliedPlan(decomposition=decomposition, plans=plans),
            bool(active) and every_query and reached == {a.filter.id for a in active})


# ------------------------------------------------------------------ the values of a filter

def values_plan(f: DashboardFilter, limit: int) -> SuppliedPlan:
    """A plan built in code for ``filter_values``: the distinct values of an ``in`` filter's column (up to
    ``limit``, in order), or ``MIN`` and ``MAX`` of a ``between`` one. It runs through the same supplied-plan path
    as a chart, so it passes the validator under the caller's scope."""
    a = Active(f, [])
    column = _column("t1", a.column)
    table = TableRef(name=a.table, schema_name=a.schema, alias="t1")
    if f.op == "between":
        select = [SelectItem(expr=Expr(kind="func", func_name=name, is_aggregate=True, args=[column]), alias=name.lower())
                  for name in ("MIN", "MAX")]
        plan = PlanModel(tables=[table], select_items=select)
    else:
        plan = PlanModel(tables=[table], distinct=True, select_items=[SelectItem(expr=column, alias=a.column)],
                         order_by=[OrderItem(expr=column)], limit=limit)
    return SuppliedPlan.for_sub_query(SubQuery(id="sq1", datasource_id=a.datasource,
                                               intent=f"The values of the {f.label} filter"), plan)


def plain(value: Any) -> Any:
    """A database value as JSON: dates as ISO text, decimals as numbers."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value
