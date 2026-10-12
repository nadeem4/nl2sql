"""Checks the string values an empty result's filter asked for.

A sub-query that returns no rows is only an answer if the values it filtered
on exist. ``Genre.Name = 'Metal'`` returns nothing because Chinook's genre is
'Heavy Metal', and the answer used to say no customer bought any. For each
string literal the plan's WHERE compares a column to (``=``, ``IN``, ``LIKE``),
a read-only probe asks whether the column has it. One that is missing gets the
closest existing values: a case-insensitive match first, then the most similar
of a bounded ``DISTINCT``. Every probe is built with sqlglot, rendered by the
adapter and capped by its row limit; a table the role may not read is never
probed.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from sqlglot import expressions as exp

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.logger import get_logger
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr, PlanModel, TableRef
from nl2sql.pipeline.nodes.generator.node import _table
from nl2sql_adapter_sdk.contracts import AdapterRequest

logger = get_logger("value_check")

# At most this many literals are probed per sub-query, and this many distinct
# values read to find a similar one (fewer if the adapter's row cap is lower).
MAX_LITERALS = 5
MAX_DISTINCT_VALUES = 500
MAX_SUGGESTIONS = 3
SIMILARITY_CUTOFF = 0.6


@dataclass(frozen=True)
class FilterLiteral:
    table: TableRef
    column: str
    value: str
    like: bool


def _text(expr: Optional[Expr]) -> Optional[str]:
    """The value of a string literal that is not a number written as a string."""
    if expr is None or expr.kind != "literal" or not isinstance(expr.value, str):
        return None
    try:
        float(expr.value)
    except ValueError:
        return expr.value
    return None


def filter_literals(plan: PlanModel) -> List[FilterLiteral]:
    """Every ``column = 'text'``, ``column IN ('text', ...)`` and ``column LIKE 'text'`` in the WHERE.

    Only through AND and OR: a negated or ``!=`` comparison is not asking for
    the value to exist, and a column inside a function is not compared as stored.
    """
    tables = {t.alias: t for t in plan.tables}
    found: List[FilterLiteral] = []

    def column_of(expr: Optional[Expr]) -> Optional[TableRef]:
        if expr is None or expr.kind != "column" or not expr.column_name:
            return None
        return tables.get(expr.alias) if expr.alias else (plan.tables[0] if len(plan.tables) == 1 else None)

    def walk(node: Optional[Expr]) -> None:
        if node is None or node.kind != "binary":
            return
        op = str(node.op).upper()
        if op in ("AND", "OR"):
            walk(node.left)
            walk(node.right)
            return
        if op in ("=", "LIKE"):
            for col, lit in ((node.left, node.right), (node.right, node.left)):
                table, value = column_of(col), _text(lit)
                if table is not None and value is not None:
                    found.append(FilterLiteral(table, col.column_name, value, op == "LIKE"))
                    return
        if op == "IN":
            table = column_of(node.left)
            items = node.right.args if node.right is not None and node.right.kind == "func" else [node.right]
            if table is not None:
                for item in items:
                    value = _text(item)
                    if value is not None:
                        found.append(FilterLiteral(table, node.left.column_name, value, False))

    walk(plan.where)
    return found


class _Probe:
    """Runs bounded, read-only queries on one column through the adapter."""

    def __init__(self, adapter: Any, literal: FilterLiteral):
        self.adapter = adapter
        self.literal = literal
        self.cap = min(int(getattr(adapter, "row_limit", None) or MAX_DISTINCT_VALUES), MAX_DISTINCT_VALUES)
        self.column = exp.column(literal.column, table=literal.table.alias)

    def _rows(self, query: exp.Select) -> List[list]:
        render = getattr(self.adapter, "render_sql", None)
        sql = render(query) if callable(render) else query.sql(dialect=self.adapter.get_dialect())
        frame = self.adapter.execute(AdapterRequest(plan_type="sql", payload={"sql": sql}))
        if not frame.success:
            raise LookupError(frame.error.safe_message if frame.error else "probe failed")
        return frame.rows

    def _select(self, *columns: exp.Expression, distinct: bool = False) -> exp.Select:
        query = exp.select(*columns).from_(_table(self.literal.table))
        return query.distinct() if distinct else query

    def _match(self, column: exp.Expression, value: exp.Expression) -> exp.Expression:
        return exp.Like(this=column, expression=value) if self.literal.like else exp.EQ(this=column, expression=value)

    def exists(self) -> bool:
        query = self._select(exp.Literal.number(1)).where(
            self._match(self.column.copy(), exp.Literal.string(self.literal.value))
        )
        return bool(self._rows(query.limit(1)))

    def closest(self) -> List[str]:
        value = exp.Literal.string(self.literal.value)
        same = self._select(self.column.copy(), distinct=True).where(
            self._match(exp.Lower(this=self.column.copy()), exp.Lower(this=value))
        )
        found = [str(r[0]) for r in self._rows(same.limit(MAX_SUGGESTIONS)) if r[0] is not None]
        if found:
            return sorted(found)
        sample = self._select(self.column.copy(), distinct=True).where(
            exp.Not(this=exp.Is(this=self.column.copy(), expression=exp.Null()))
        )
        values = [str(r[0]) for r in self._rows(sample.limit(self.cap)) if r[0] is not None]
        target = self.literal.value.strip("%_").lower() if self.literal.like else self.literal.value.lower()
        by_lower = {v.lower(): v for v in values}
        close = difflib.get_close_matches(target, list(by_lower), n=MAX_SUGGESTIONS, cutoff=SIMILARITY_CUTOFF)
        return [by_lower[c] for c in close]


def _quoted(values: List[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def check_values(
    plan: PlanModel,
    adapter: Any,
    allowed: Callable[[str], bool],
    can_retry: bool,
) -> List[PipelineError]:
    """A ``VALUE_NOT_FOUND`` per filter value the column does not have.

    With a close value and ``can_retry``, it is a retryable ERROR telling the
    planner which values exist. Otherwise it is a WARNING the answer reports:
    "There is no Genre with Name 'Polka'." A literal whose probe fails, or
    whose table ``allowed`` refuses, is not reported: nothing is claimed
    without having looked.
    """
    errors: List[PipelineError] = []
    for literal in filter_literals(plan)[:MAX_LITERALS]:
        if not allowed(literal.table.name):
            continue
        probe = _Probe(adapter, literal)
        try:
            if probe.exists():
                continue
            suggestions = probe.closest()
        except LookupError as exc:
            logger.info("Value probe skipped for %s.%s: %s", literal.table.name, literal.column, exc)
            continue

        table, column, value = literal.table.name, literal.column, literal.value
        if suggestions and can_retry:
            matches = "matches" if literal.like else "is"
            errors.append(PipelineError(
                node="executor",
                message=(
                    f"No {table}.{column} value {matches} '{value}', so the query returned no rows. "
                    f"The closest existing values are {_quoted(suggestions)}; use one of them if it is "
                    "what the question means."
                ),
                severity=ErrorSeverity.ERROR,
                error_code=ErrorCode.VALUE_NOT_FOUND,
            ))
            continue
        message = f"There is no {table} with {column} '{value}'"
        if suggestions:
            message += f"; the closest {'is' if len(suggestions) == 1 else 'are'} {_quoted(suggestions)}"
        errors.append(PipelineError(
            node="executor",
            message=message + ".",
            severity=ErrorSeverity.WARNING,
            error_code=ErrorCode.VALUE_NOT_FOUND,
        ))
    return errors
