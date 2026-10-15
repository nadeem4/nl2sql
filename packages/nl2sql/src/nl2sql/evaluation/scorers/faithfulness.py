"""``faithfulness``: every number and quoted name the answer states is in the result.

The deterministic check in :mod:`nl2sql.evaluation.faithfulness`, no model.
Applies to a run that wrote an answer; mode ``full`` only (mode ``plans``
writes a fixed sentence).
"""
from __future__ import annotations

from typing import ClassVar, FrozenSet

from nl2sql.evaluation.faithfulness import answer_text, check_answer
from nl2sql.evaluation.scorers import NOT_APPLICABLE, BuiltinScorer, CaseRun, Score


class Faithfulness(BuiltinScorer):
    modes: ClassVar[FrozenSet[str]] = frozenset({"full"})

    def score(self, run: CaseRun) -> Score:
        text = answer_text(run.result.final_answer)
        if not text:
            return NOT_APPLICABLE
        samples = [sq.rows for sq in run.result.sub_queries if sq.rows is not None]
        check = check_answer(text, columns=[c for s in samples for c in s.columns],
                             rows=[r for s in samples for r in s.rows], question=run.case.question,
                             row_counts=[s.total_rows for s in samples])
        detail = dict(check)
        if not check["faithful"]:
            detail["reason"] = ("the answer states what the rows do not: "
                                + ", ".join(check["unsupported_numbers"] + check["unsupported_entities"]))
        return Score(value=1.0 if check["faithful"] else 0.0, passed=check["faithful"], detail=detail)
