from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple, Union

import sqlglot

from nl2sql.api.query_api import QueryResult
from nl2sql.evaluation.gold import GoldQuestion
from nl2sql.pipeline.nodes.validator.node import REFUSAL_MESSAGE

# Gold values are rounded to two decimals, so an unrounded engine value can sit
# up to half a cent away from the gold one. The epsilon absorbs float noise at
# exactly half a cent.
NUMERIC_TOLERANCE = 0.005 + 1e-9

OUTCOMES = ("pass", "fail", "skip", "xfail")

Row = Union[Dict[str, Any], Sequence[Any]]


def _values(row: Row) -> List[Any]:
    """A row's values in selected order; a mapping keeps its key order."""
    return list(row.values()) if isinstance(row, dict) else list(row)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


def _same_value(got: Any, want: Any) -> bool:
    # A number matches a string that spells it ('2009' == 2009): databases differ
    # in which of the two a date part or a computed value comes back as. Two
    # strings still compare as strings.
    if _is_number(got) or _is_number(want):
        g, w = _as_number(got), _as_number(want)
        if g is not None and w is not None:
            return math.isclose(g, w, rel_tol=0.0, abs_tol=NUMERIC_TOLERANCE)
    return type(got) is type(want) and got == want


def _same_row(got: List[Any], want: List[Any]) -> bool:
    return len(got) == len(want) and all(_same_value(g, w) for g, w in zip(got, want))


class ModelEvaluator:
    """Evaluates the correctness of AI-generated SQL and its execution results."""

    @staticmethod
    def compare_sql_semantic(generated_sql: str, expected_sql: str) -> bool:
        """Compares two SQL queries semantically by normalizing them to ASTs.

        Raises:
            ValueError: If either SQL query is invalid or unparseable.
        """
        if not generated_sql or not expected_sql:
            return False

        if generated_sql.strip() == expected_sql.strip():
            return True

        try:
            gen_ast = sqlglot.parse_one(generated_sql)
        except Exception as e:
            raise ValueError(f"Generated SQL is invalid/unparseable: {e}")

        try:
            exp_ast = sqlglot.parse_one(expected_sql)
        except Exception as e:
            raise ValueError(f"Ground Truth SQL is invalid/unparseable: {e}")

        return gen_ast.sql() == exp_ast.sql()

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
    def score_case(question: GoldQuestion, role: str, result: QueryResult) -> Tuple[str, str]:
        """Scores one run of ``question`` as ``role``: ``(status, reason)``.

        ``allowed`` passes when the run succeeds and its rows match
        ``gold_result`` (respecting ``order_matters``). ``refused`` passes on
        the RBAC refusal: status ``error``, a ``SECURITY_VIOLATION`` whose
        message is the generic one, and no rows. ``unanswerable`` passes on the
        datasource resolver's refusal: status ``error`` with
        ``QUESTION_NOT_ANSWERABLE`` and no rows.
        """
        expected = question.expected[role]
        errors = result.errors
        first_error = f"{errors[0].get('error_code')}: {errors[0].get('message')}" if errors else ""
        row_samples = [sq.rows for sq in result.sub_queries if sq.rows is not None]
        returned_rows = any(sample.total_rows or sample.rows for sample in row_samples)

        if expected == "unanswerable":
            codes = {e.get("error_code") for e in errors}
            if returned_rows or result.status != "error" or "QUESTION_NOT_ANSWERABLE" not in codes:
                return "fail", (f"expected QUESTION_NOT_ANSWERABLE, got status '{result.status}': "
                                f"{first_error or f'rows={returned_rows}'}")
            return "pass", ""

        if expected == "refused":
            denials = [e for e in errors if e.get("error_code") == "SECURITY_VIOLATION"]
            if returned_rows or result.status != "error":
                return "fail", f"expected a refusal, got status '{result.status}' with rows={returned_rows}"
            if not denials:
                return "fail", f"expected a refusal, got {first_error or 'no error'}"
            if any(e.get("message") != REFUSAL_MESSAGE for e in denials):
                return "fail", f"refusal is not the generic message: {denials[0].get('message')}"
            return "pass", ""

        if result.status != "success":
            return "fail", f"status '{result.status}': {first_error or 'no rows'}"
        if len(row_samples) != 1:
            return "fail", f"expected one result set, got {len(row_samples)}"
        sample = row_samples[0]
        gold = question.gold_result or []
        if not ModelEvaluator.compare_results(sample.rows, gold, order_matters=question.order_matters):
            return "fail", f"rows differ from gold_result ({len(sample.rows)} rows, gold has {len(gold)})"
        return "pass", ""

    @staticmethod
    def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Counts each outcome per role and in total."""
        def empty() -> Dict[str, int]:
            return {outcome: 0 for outcome in OUTCOMES}

        by_role: Dict[str, Dict[str, int]] = {}
        total = empty()
        for r in results:
            by_role.setdefault(r["role"], empty())[r["status"]] += 1
            total[r["status"]] += 1
        return {"by_role": by_role, "total": total}
