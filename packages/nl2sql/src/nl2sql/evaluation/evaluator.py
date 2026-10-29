"""Comparing a result's rows with a case's reviewed rows, strictly and leniently.

The ``result_match`` scorer reads both (``nl2sql.evaluation.scorers.result_match``).
"""
from __future__ import annotations

import datetime as dt
import itertools
import math
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Union

import sqlglot


# Gold values are rounded to two decimals, so an unrounded engine value can sit
# up to half a cent away from the gold one. The epsilon absorbs float noise at
# exactly half a cent.
NUMERIC_TOLERANCE = 0.005 + 1e-9

Row = Union[Dict[str, Any], Sequence[Any]]


def _values(row: Row) -> List[Any]:
    """A row's values in selected order; a mapping keeps its key order."""
    return list(row.values()) if isinstance(row, dict) else list(row)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _as_number(value: Any) -> Any:
    """``value`` as a float if it is a number or a string spelling a finite one, else None."""
    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


# A date or a date and time, as ISO 8601 spells it ("2022-01-01", "2022-01-01T00:00:00Z").
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][\d:.]+(Z|[+-]\d{2}:?\d{2})?)?$")


def _as_time(value: Any):
    """``(datetime, date_only)`` if ``value`` is a date, a datetime or an ISO string spelling one, else None.

    A datetime keeps its own offset; one with none is read as UTC. ``date_only`` is
    True for a ``date`` and for a ``YYYY-MM-DD`` string. Gold rows are written as
    JSON, so they hold ISO strings where a live row holds a ``date`` or ``datetime``.
    """
    if isinstance(value, str):
        text = value.strip()
        if not _ISO_DATE.match(text):
            return None
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)), len(text) == 10
    if isinstance(value, dt.datetime):
        return (value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)), False
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc), True
    return None


def _same_time(got: Any, want: Any) -> Optional[bool]:
    """Whether two dates or datetimes are the same moment, or None when either is not one.

    Two full datetimes are equal as instants. A date-only value is a calendar day:
    it equals a datetime that is midnight of that day in the datetime's own offset
    (``2021-12-31T19:00-05:00`` is not ``2022-01-01``, though it is the same instant
    as midnight UTC), and another date-only value of the same day.
    """
    g, w = _as_time(got), _as_time(want)
    if g is None or w is None:
        return None
    (g_time, g_day), (w_time, w_day) = g, w
    if g_day or w_day:
        full = w_time if g_day else g_time
        day = g_time if g_day else w_time
        if g_day and w_day:
            return g_time.date() == w_time.date()
        return full.date() == day.date() and full.time() == dt.time(0)
    return g_time == w_time


def _same_value(got: Any, want: Any) -> bool:
    # A number matches a string that spells it ('2009' == 2009): databases differ
    # in which of the two a date part or a computed value comes back as. Two
    # strings still compare as strings.
    if _is_number(got) or _is_number(want):
        g, w = _as_number(got), _as_number(want)
        if g is not None and w is not None:
            return math.isclose(g, w, rel_tol=0.0, abs_tol=NUMERIC_TOLERANCE)
    # A date or datetime matches its ISO string, and a date-only label its midnight.
    same_time = _same_time(got, want)
    if same_time is not None:
        return same_time
    return type(got) is type(want) and got == want


def _same_row(got: List[Any], want: List[Any]) -> bool:
    return len(got) == len(want) and all(_same_value(g, w) for g, w in zip(got, want))


# -- the lenient comparison -------------------------------------------------
#
# Spider 2.0 counts a prediction correct when every gold column vector appears
# in the result (https://arxiv.org/html/2411.07763v2), and Defog's sql-eval
# falls back to ``subset_df``, matching each gold column's values against some
# generated column with names, types and positions ignored
# (https://defog.ai/blog/open-sourcing-sqleval/). The lenient score here is
# that rule, with two guardrails against the false positives it can let in.

# A gold period label: a year, a year and month, or a full date.
_PERIOD_LABEL = re.compile(r"^\d{4}(-\d{2}){0,2}$")
# The predicted value it may be compared with: a date, optionally with a time.
_DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _same_period(got: Any, want: Any) -> bool:
    """A gold period label against a predicted date that starts with it.

    ``'2009'`` matches ``'2009-01-01'`` and ``'2013-02'`` matches
    ``'2013-02-01'``: the same period, labelled by its first day rather than
    truncated. Only the gold side may be the shorter label, only strings are
    read this way (a number is a number, never a year), and the prefix must end
    on a component boundary, so ``'2009-1'`` matches nothing.
    """
    if isinstance(got, dt.date):
        got = got.isoformat()
    if not isinstance(got, str) or not isinstance(want, str):
        return False
    if not _PERIOD_LABEL.match(want) or not _DATE_VALUE.match(got):
        return False
    return got == want or got.startswith(want + "-")


def _same_value_lenient(got: Any, want: Any) -> bool:
    return _same_value(got, want) or _same_period(got, want)


def _same_row_lenient(got: List[Any], want: List[Any]) -> bool:
    return len(got) == len(want) and all(_same_value_lenient(g, w) for g, w in zip(got, want))


def _rows_match(got: List[List[Any]], want: List[List[Any]], order_matters: bool) -> bool:
    """``compare_results``' row rule, with period labels normalised."""
    if order_matters:
        return all(_same_row_lenient(g, w) for g, w in zip(got, want))
    unused = list(want)
    for row in got:
        match = next((i for i, w in enumerate(unused) if _same_row_lenient(row, w)), None)
        if match is None:
            return False
        unused.pop(match)
    return True


def _column(rows: Sequence[List[Any]], index: int) -> List[Any]:
    return [row[index] for row in rows]


def _is_constant(column: Sequence[Any]) -> bool:
    """Every value the same, all-null included. One row makes every column constant."""
    return all(_same_value(v, column[0]) for v in column[1:])


def _column_can_match(got: List[Any], want: List[Any]) -> bool:
    """Whether a result column could stand in for a gold column.

    Its values must be the gold column's as a multiset. A constant or all-null
    result column is refused outright once there is more than one row: it has
    the right height and tells the gold column nothing, so a hard-coded
    literal must not be read as a computed one. On a single row every column
    is constant and the rule is off; the column cap and the values carry it
    there.
    """
    if len(got) > 1 and _is_constant(got):
        return False
    unused = list(got)
    for value in want:
        match = next((i for i, g in enumerate(unused) if _same_value_lenient(g, value)), None)
        if match is None:
            return False
        unused.pop(match)
    return True


class ModelEvaluator:
    """Evaluates the correctness of AI-generated SQL and its execution results."""

    @staticmethod
    def compare_sql_semantic(generated_sql: str, expected_sql: str, dialect: Optional[str] = None) -> bool:
        """Compares two SQL queries semantically by normalizing them to ASTs.

        ``dialect`` is the datasource's sqlglot dialect (``adapter.get_dialect()``);
        without it, T-SQL's ``TOP`` and ``[ident]`` or MySQL's backticks misparse.

        Raises:
            ValueError: If either SQL query is invalid or unparseable.
        """
        if not generated_sql or not expected_sql:
            return False

        if generated_sql.strip() == expected_sql.strip():
            return True

        try:
            gen_ast = sqlglot.parse_one(generated_sql, read=dialect)
        except Exception as e:
            raise ValueError(f"Generated SQL is invalid/unparseable: {e}")

        try:
            exp_ast = sqlglot.parse_one(expected_sql, read=dialect)
        except Exception as e:
            raise ValueError(f"Ground Truth SQL is invalid/unparseable: {e}")

        return gen_ast.sql(dialect=dialect) == exp_ast.sql(dialect=dialect)

    @staticmethod
    def compare_results(
        generated_rows: Sequence[Row],
        expected_rows: Sequence[Row],
        order_matters: bool = False,
    ) -> bool:
        """Whether two result sets hold the same values.

        Column names and aliases are ignored: each row is compared as its
        values in selected order, so ``SELECT Country AS c`` matches a gold
        ``Country`` column but a swapped column order does not. Numbers match
        within ``NUMERIC_TOLERANCE``, and a number matches a string that spells
        one, so ``'2009'`` is ``2009``; anything else must be equal and of the
        same type. Without ``order_matters``
        each generated row is paired with one unused equal gold row, which
        needs no sorting and so copes with ``None`` and mixed types.
        """
        got = [_values(r) for r in generated_rows]
        want = [_values(r) for r in expected_rows]
        if len(got) != len(want):
            return False

        if order_matters:
            return all(_same_row(g, w) for g, w in zip(got, want))

        unused = list(want)
        for row in got:
            match = next((i for i, w in enumerate(unused) if _same_row(row, w)), None)
            if match is None:
                return False
            unused.pop(match)
        return True

    @staticmethod
    def compare_results_extra_columns(
        generated_rows: Sequence[Row],
        expected_rows: Sequence[Row],
        order_matters: bool = False,
    ) -> bool:
        """Whether the result is the gold rows plus extra columns, and nothing looser.

        The result must be wider than the gold, within :meth:`lenient_column_cap`,
        and some choice of its columns *in their own order* must equal the gold
        under :meth:`compare_results`: the gold's columns appear in the same order
        and match strictly, so reordered columns and period labels do not pass.
        The extra columns may sit anywhere.
        """
        got = [_values(r) for r in generated_rows]
        want = [_values(r) for r in expected_rows]
        if len(got) != len(want) or not want or not got:
            return False
        width, gold_width = len(got[0]), len(want[0])
        if width <= gold_width or width > ModelEvaluator.lenient_column_cap(gold_width):
            return False
        return any(ModelEvaluator.compare_results([[row[i] for i in picks] for row in got], want, order_matters)
                   for picks in itertools.combinations(range(width), gold_width))

    @staticmethod
    def lenient_column_cap(gold_columns: int) -> int:
        """How many columns a lenient match lets a result carry: ``2x`` the gold's, or two more.

        Whichever is larger, so a one-column gold answer may come back with up
        to three columns and a three-column one with up to six. Without a cap a
        ``SELECT *`` would pass by carrying the gold columns among many others;
        Databricks Genie counts any extra column as bad, so a cap is the middle
        ground (https://docs.databricks.com/aws/en/genie/benchmarks).
        """
        return max(2 * gold_columns, gold_columns + 2)

    @staticmethod
    def compare_results_lenient(
        generated_rows: Sequence[Row],
        expected_rows: Sequence[Row],
        order_matters: bool = False,
    ) -> bool:
        """Whether every gold column is answered by a distinct column of the result.

        Everything :meth:`compare_results` accepts is accepted here. Beyond it,
        a result may carry its columns in another order and carry extra ones,
        up to :meth:`lenient_column_cap`; each gold column must be matched, as
        a multiset of values, by a distinct result column, and the rows of the
        matched columns must then line up as ``compare_results`` requires --
        so values swapped between rows still fail. A constant or all-null
        result column may only answer for a constant gold column. Gold period
        labels are read against dates (``'2009'`` is ``'2009-01-01'``);
        nothing else is normalised. The row count must be equal, and row order
        still counts only when ``order_matters``.
        """
        if ModelEvaluator.compare_results(generated_rows, expected_rows, order_matters):
            return True
        got = [_values(r) for r in generated_rows]
        want = [_values(r) for r in expected_rows]
        if len(got) != len(want) or not want:
            return False

        width, gold_width = len(got[0]), len(want[0])
        if gold_width > width or width > ModelEvaluator.lenient_column_cap(gold_width):
            return False

        columns = [_column(got, i) for i in range(width)]
        candidates = [[i for i in range(width) if _column_can_match(columns[i], _column(want, j))]
                      for j in range(gold_width)]
        if any(not c for c in candidates):
            return False
        for picks in itertools.product(*candidates):
            if len(set(picks)) != gold_width:
                continue
            if _rows_match([[row[i] for i in picks] for row in got], want, order_matters):
                return True
        return False
