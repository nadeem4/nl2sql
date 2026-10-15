"""``result_match``: the rows match the case's ``rows`` or one of its ``alt_rows``.

Applies to a role whose outcome is ``allowed``, on a case of kind
``answer``: a routing case says where a question goes, not what it returns,
and is left out of execution accuracy. Strict: column names are
ignored, column order is kept, numbers match within half a cent, and row
order counts only when the case's ``order_matters``
(:meth:`ModelEvaluator.compare_results`). The lenient reading, which also
accepts reordered and a few extra columns and period labels against dates
(:meth:`ModelEvaluator.compare_results_lenient`), is kept in ``detail``
beside it, never in place of it.
"""
from __future__ import annotations

from typing import ClassVar, FrozenSet

from nl2sql.evaluation.evaluator import ModelEvaluator
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
        lenient = strict or any(ModelEvaluator.compare_results_lenient(run.rows, a, order_matters=order)
                                for a in answers)
        detail = {"lenient": lenient}
        if not strict:
            extra = len(answers) - 1
            alternatives = f" and {extra} alternative{'s' if extra != 1 else ''}" if extra else ""
            detail["reason"] = (f"rows differ from the case's rows{alternatives} "
                                f"({len(run.rows)} rows, the case has {len(answers[0])})")
        return Score(value=1.0 if strict else 0.0, passed=strict, detail=detail)
