"""``outcome``: the role got what the case says it should.

* ``allowed``: the run succeeded (``result_match`` checks its rows);
* ``refused``: the validator's refusal, with the generic message and no rows;
* ``unanswerable``: the resolver's ``QUESTION_NOT_ANSWERABLE`` and no rows;
* ``clarify``: the run asked for clarification (status ``needs_clarification``)
  and returned no rows. Runs only with ``engine.evals.run(clarify=True)``.
"""
from __future__ import annotations

from typing import ClassVar, FrozenSet

from nl2sql.evaluation.scorers import BuiltinScorer, CaseRun, Score
from nl2sql.pipeline.nodes.validator.node import REFUSAL_MESSAGE


def _verdict(passed: bool, reason: str = "") -> Score:
    return Score(value=1.0 if passed else 0.0, passed=passed, detail={"reason": reason} if reason else {})


class Outcome(BuiltinScorer):
    modes: ClassVar[FrozenSet[str]] = frozenset({"full", "plans"})

    def score(self, run: CaseRun) -> Score:
        result = run.result
        errors = result.errors
        first = f"{errors[0].get('error_code')}: {errors[0].get('message')}" if errors else ""
        returned_rows = any(sq.rows is not None and (sq.rows.total_rows or sq.rows.rows)
                            for sq in result.sub_queries)
        expected = run.expected

        if expected == "unanswerable":
            codes = {e.get("error_code") for e in errors}
            if returned_rows or result.status != "error" or "QUESTION_NOT_ANSWERABLE" not in codes:
                return _verdict(False, f"expected QUESTION_NOT_ANSWERABLE, got status '{result.status}': "
                                       f"{first or f'rows={returned_rows}'}")
            return _verdict(True)

        if expected == "refused":
            denials = [e for e in errors if e.get("error_code") == "SECURITY_VIOLATION"]
            if returned_rows or result.status != "error":
                return _verdict(False, f"expected a refusal, got status '{result.status}' with rows={returned_rows}")
            if not denials:
                return _verdict(False, f"expected a refusal, got {first or 'no error'}")
            if any(e.get("message") != REFUSAL_MESSAGE for e in denials):
                return _verdict(False, f"refusal is not the generic message: {denials[0].get('message')}")
            return _verdict(True)

        if expected == "clarify":
            if returned_rows or result.status != "needs_clarification":
                return _verdict(False, f"expected needs_clarification, got status '{result.status}'")
            return _verdict(True)

        if result.status != "success":
            return _verdict(False, f"status '{result.status}': {first or 'no rows'}")
        return _verdict(True)
