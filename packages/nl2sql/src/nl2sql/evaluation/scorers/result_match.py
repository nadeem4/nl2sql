"""``result_match``: the rows match the case's ``rows`` or one of its ``alt_rows``.

The rows are the run's terminal result (the combined one for a question the
decomposer split), not a sub-query's scan; two terminal results fail as "expected
one result set".

Applies to a role whose outcome is ``allowed``, on a case of kind
``answer``: a routing case says where a question goes, not what it returns,
and is left out of execution accuracy. Column names are
ignored, numbers (``Decimal`` too) match within half a
cent, dates match their ISO strings, and row order counts only when the case's
``order_matters`` (:meth:`ModelEvaluator.compare_results`). A result with the
right rows in another column order, or with extra columns, also passes
(:meth:`ModelEvaluator.column_picks`: each gold column is answered by a distinct
result column with equal values, so a mislabelled column fails; a result of more
than 12 columns is not searched). Period labels against dates alone do not pass;
they are kept in ``detail`` as ``lenient``, with ``match`` naming how the case
passed: ``strict``, ``reordered``, ``extra columns``, ``extra columns, reordered``
or none.
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
        if len(run.sets) > 1:
            return Score(value=0.0, passed=False, detail={"reason": f"expected one result set, got {len(run.sets)}",
                                                          "lenient": False})
        answers = run.case.answers()
        order = run.case.order_matters
        strict = any(ModelEvaluator.compare_results(run.rows, a, order_matters=order) for a in answers)
        lenient = strict or any(ModelEvaluator.compare_results_lenient(run.rows, a, order_matters=order)
                                for a in answers)
        # The right rows in another column order, or with extra columns, also pass; a period label alone stays lenient-only.
        picks = None if strict else next((p for p in (ModelEvaluator.column_picks(run.rows, a, order_matters=order)
                                                      for a in answers) if p is not None), None)
        passed = strict or picks is not None
        match = None
        if strict:
            match = "strict"
        elif picks is not None:
            reordered = list(picks) != sorted(picks)
            wider = len(run.rows[0]) > len(picks)
            match = ("extra columns, reordered" if reordered else "extra columns") if wider else "reordered"
        detail = {"lenient": lenient, "match": match}
        if not passed:
            extra = len(answers) - 1
            alternatives = f" and {extra} alternative{'s' if extra != 1 else ''}" if extra else ""
            detail["reason"] = (f"rows differ from the case's rows{alternatives} "
                                f"({len(run.rows)} rows, the case has {len(answers[0])})")
        return Score(value=1.0 if passed else 0.0, passed=passed, detail=detail)
