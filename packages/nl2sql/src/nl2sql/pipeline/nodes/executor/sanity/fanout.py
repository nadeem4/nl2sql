"""Join fan-out: an aggregate over rows a join repeated (``JOIN_FAN_OUT``).

Joining Customer to Invoice on ``Invoice.CustomerId`` repeats each customer
once per invoice, so ``COUNT(c.CustomerId)`` counts invoices and
``SUM(c.Credit)`` adds each customer's credit several times. A table's rows
are repeated when some join in the plan reaches, on its far side, a key that
is not unique there (not the far table's whole primary key, from the
snapshot). Taking one join out of the plan's tree splits the tables in two;
every table on the near side is repeated. This covers a many-to-many sum too:
Invoice and Ticket both joined to Customer repeat each invoice once per ticket.

A suspected fan-out is confirmed by a probe over the plan's own FROM, joins
and WHERE: ``COUNT(*)`` against ``COUNT(DISTINCT <the repeated table's primary
key>)``. It is a sqlglot tree rendered by the adapter, returns one row, and
runs only when the role may read every table in the plan. A table with no
known single-column primary key is not probed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sqlglot import expressions as exp

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.logger import get_logger
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, PlanModel, TableRef
from nl2sql.pipeline.nodes.generator.node import GeneratorNode, SqlVisitor, _table
from nl2sql_sdk.contracts import AdapterRequest

logger = get_logger("sanity.fanout")

# The aggregates a repeated row miscounts. MIN and MAX are not changed by a repeat.
_AGGREGATES = ("COUNT", "SUM", "AVG")


@dataclass(frozen=True)
class _Suspect:
    function: str
    alias: str
    column: str
    repeated_by: str  # the alias of the table whose non-unique key repeats the rows


def _keys(tables: List[Any]) -> Dict[str, List[str]]:
    """Each table's primary key from the snapshot, by lower-case table name."""
    return {str(t.name).lower(): [str(c).lower() for c in (t.primary_key or [])] for t in tables}


def _join_columns(condition: Optional[Expr]) -> List[Tuple[str, str, str, str]]:
    """``(alias, column, alias, column)`` for each ``a.x = b.y`` in a join condition, through AND."""
    if condition is None or condition.kind != "binary":
        return []
    op = str(condition.op).upper()
    if op == "AND":
        return _join_columns(condition.left) + _join_columns(condition.right)
    left, right = condition.left, condition.right
    if op == "=" and all(e is not None and e.kind == "column" and e.alias for e in (left, right)):
        return [(left.alias, str(left.column_name).lower(), right.alias, str(right.column_name).lower())]
    return []


def _unique(alias: str, columns: Set[str], declared: Dict[str, TableRef], keys: Dict[str, List[str]]) -> bool:
    """Whether ``columns`` hold the whole primary key of ``alias``'s table. An unknown key counts as unique."""
    key = keys.get(declared[alias].name.lower()) if alias in declared else None
    return not key or set(key) <= columns


def _near_side(edges: List[Tuple[str, str]], cut: int, start: str) -> Set[str]:
    """The aliases still reached from ``start`` with edge ``cut`` taken out."""
    seen, todo = {start}, [start]
    while todo:
        here = todo.pop()
        for i, (a, b) in enumerate(edges):
            if i == cut or here not in (a, b):
                continue
            other = b if here == a else a
            if other not in seen:
                seen.add(other)
                todo.append(other)
    return seen


def _repeated(plan: PlanModel, keys: Dict[str, List[str]]) -> Dict[str, str]:
    """Each repeated alias, with the alias whose non-unique key repeats it."""
    declared = {t.alias: t for t in plan.tables}
    edges = [(j.left_alias, j.right_alias) for j in plan.joins]
    repeated: Dict[str, str] = {}
    for i, join in enumerate(plan.joins):
        sides: Dict[str, Set[str]] = {join.left_alias: set(), join.right_alias: set()}
        for a, col_a, b, col_b in _join_columns(join.condition):
            if a in sides:
                sides[a].add(col_a)
            if b in sides:
                sides[b].add(col_b)
        for near, far in ((join.left_alias, join.right_alias), (join.right_alias, join.left_alias)):
            if not _unique(far, sides[far], declared, keys):
                for alias in _near_side(edges, i, near):
                    repeated.setdefault(alias, far)
    return repeated


def _suspects(plan: PlanModel, keys: Dict[str, List[str]]) -> List[_Suspect]:
    if not plan.joins:
        return []
    repeated = _repeated(plan, keys)
    found: List[_Suspect] = []
    for item in plan.select_items:
        e = item.expr
        name = str(e.func_name or "").upper()
        if e.kind != "func" or name not in _AGGREGATES or e.distinct or len(e.args) != 1:
            continue
        arg = e.args[0]
        if arg.kind == "column" and arg.alias in repeated:
            found.append(_Suspect(name, arg.alias, str(arg.column_name), repeated[arg.alias]))
    return found


def _probe(plan: PlanModel, adapter: Any, alias: str, key: str) -> Tuple[int, int]:
    """``(rows, distinct keys of alias)`` over the plan's FROM, joins and WHERE."""
    visitor = SqlVisitor()
    declared = {t.alias: t for t in plan.tables}
    query = exp.select(
        exp.Count(this=exp.Star()),
        exp.Count(this=exp.Distinct(expressions=[exp.column(key, table=alias)])),
    ).from_(_table(plan.tables[0]))
    query = GeneratorNode.attach_joins(query, plan, declared, plan.tables[0].alias, visitor)
    if plan.where:
        query = query.where(visitor.visit(plan.where))
    query = query.limit(1)
    render = getattr(adapter, "render_sql", None)
    sql = render(query) if callable(render) else query.sql(dialect=adapter.get_dialect())
    frame = adapter.execute(AdapterRequest(plan_type="sql", payload={"sql": sql}))
    if not frame.success or not frame.rows:
        raise LookupError(frame.error.safe_message if frame.error else "probe failed")
    rows, distinct = frame.rows[0][0], frame.rows[0][1]
    return int(rows or 0), int(distinct or 0)


def check_fan_out(
    plan: PlanModel,
    adapter: Any,
    allowed: Callable[[TableRef], bool],
    tables: List[Any],
    can_retry: bool,
) -> List[PipelineError]:
    """A ``JOIN_FAN_OUT`` for each aggregate over rows a join repeated, once the probe confirms it.

    ``tables`` are the sub-query's tables from the snapshot (``relevant_tables``),
    with their primary keys. Under ``COUNT`` with ``can_retry`` it is a
    retryable ERROR telling the planner to count distinct keys; under ``SUM``
    or ``AVG``, or with no retry left, a WARNING the answer reports. A probe
    that fails, or a table the role may not read, reports nothing.
    """
    keys = _keys(tables)
    suspects = _suspects(plan, keys)
    if not suspects or not all(allowed(t) for t in plan.tables):
        return []
    declared = {t.alias: t for t in plan.tables}
    confirmed: Dict[str, Optional[Tuple[int, int]]] = {}
    errors: List[PipelineError] = []
    for s in suspects:
        table = declared[s.alias].name
        key = keys.get(table.lower()) or []
        if len(key) != 1:
            continue
        if s.alias not in confirmed:
            try:
                rows, distinct = _probe(plan, adapter, s.alias, key[0])
                confirmed[s.alias] = (rows, distinct) if rows > distinct else None
            except LookupError as exc:
                logger.info("Fan-out probe skipped for %s: %s", table, exc)
                confirmed[s.alias] = None
        counts = confirmed[s.alias]
        if counts is None:
            continue
        rows, distinct = counts
        by = declared[s.repeated_by].name
        if s.function == "COUNT" and can_retry:
            errors.append(PipelineError(
                node="executor",
                message=(f"COUNT({table}.{s.column}) counts each {table} row once per matching {by} row: the "
                         f"joins give {rows} rows for {distinct} {table} rows. Count distinct values instead "
                         f"(set distinct on the COUNT)."),
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.JOIN_FAN_OUT,
            ))
            continue
        errors.append(PipelineError(
            node="executor",
            message=(f"{s.function}({table}.{s.column}) may count each {table} row more than once: joining "
                     f"{by} repeats them ({rows} rows for {distinct} {table} rows)."),
            severity=ErrorSeverity.WARNING,
            error_code=ErrorCode.JOIN_FAN_OUT,
        ))
    return errors
