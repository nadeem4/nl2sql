"""``result_match``: the rows match the case's ``rows`` or one of its ``alt_rows``.

Applies to a role whose outcome is ``allowed``, on a case of kind
``answer``: a routing case says where a question goes, not what it returns,
and is left out of execution accuracy. Column names are
ignored, column order is kept, numbers (``Decimal`` too) match within half a
cent, dates match their ISO strings, and row order counts only when the case's
``order_matters`` (:meth:`ModelEvaluator.compare_results`). A result with the
right rows plus extra columns also passes (the lenient reading,
:meth:`ModelEvaluator.compare_results_lenient`, on a result wider than the gold).
Reordered columns and period labels against dates alone do not pass; they are
kept in ``detail`` as ``lenient``, with ``match`` naming how the case passed:
``strict``, ``extra columns`` or none.
"""
from __future__ import annotations

from typing import ClassVar, FrozenSet

from nl2sql.evaluation.evaluator import ModelEvaluator, _values
from nl2sql.evaluation.scorers import NOT_APPLICABLE, BuiltinScorer, CaseRun, Score


class ResultMatch(BuiltinScorer):
    modes: ClassVar[FrozenSet[str]] = frozenset({"full", "plans"})

    def score(self, run: CaseRun) -> Score:
        if run.expected != "allowed" or run.case.kind == "routing":
            return NOT_APPLICABLE
        if run.rows is None:
            return Score(value=0.0, passed=False, detail={"reason": f"no rows (status '{run.result.status}')",
                                                          "lenient": False})
        sets = sum(1 for sq in run.result.sub_queries if sq.rows is not None)
        if sets > 1:
            return Score(value=0.0, passed=False, detail={"reason": f"expected one result set, got {sets}",
                                                          "lenient": False})
        answers = run.case.answers()
        order = run.case.order_matters
        strict = any(ModelEvaluator.compare_results(run.rows, a, order_matters=order) for a in answers)
        loose = [a for a in answers if not strict
                 and ModelEvaluator.compare_results_lenient(run.rows, a, order_matters=order)]
        lenient = strict or bool(loose)
        # The right rows with extra columns pass; a reordering or a period label alone stays lenient-only.
        extra = any(a and run.rows and len(_values(run.rows[0])) > len(_values(a[0])) for a in loose)
        passed = strict or extra
        detail = {"lenient": lenient, "match": "strict" if strict else "extra columns" if extra else None}
        if not passed:
            extra = len(answers) - 1
            alternatives = f" and {extra} alternative{'s' if extra != 1 else ''}" if extra else ""
            detail["reason"] = (f"rows differ from the case's rows{alternatives} "
                                f"({len(run.rows)} rows, the case has {len(answers[0])})")
        return Score(value=1.0 if passed else 0.0, passed=passed, detail=detail)
