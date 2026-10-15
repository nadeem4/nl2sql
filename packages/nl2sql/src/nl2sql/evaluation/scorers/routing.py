"""``routing``: the question went to the case's ``datasources``.

Mode ``full``: the datasources the run's sub-queries used are exactly the
case's. Mode ``retrieval``: every one of the case's datasources is among
those routing ranked, which the runner cuts at
``settings.routing.max_datasources``. Applies to a role whose outcome is
``allowed``.
"""
from __future__ import annotations

from typing import ClassVar, FrozenSet

from nl2sql.evaluation.scorers import NOT_APPLICABLE, BuiltinScorer, CaseRun, Score


class Routing(BuiltinScorer):
    modes: ClassVar[FrozenSet[str]] = frozenset({"full", "retrieval"})

    def score(self, run: CaseRun) -> Score:
        if run.expected != "allowed" or not run.case.datasources:
            return NOT_APPLICABLE
        wanted = sorted(set(run.case.datasources))
        if run.mode == "retrieval":
            if run.routed is None:
                return NOT_APPLICABLE
            missed = [d for d in wanted if d not in run.routed]
            detail = {"routed": list(run.routed), "missed": missed}
            if missed:
                detail["reason"] = f"routing ranked {run.routed}, missing {missed}"
            return Score(value=1.0 if not missed else 0.0, passed=not missed, detail=detail)
        used = sorted({sq.datasource_id for sq in run.result.sub_queries if sq.datasource_id})
        passed = used == wanted
        detail = {"used": used}
        if not passed:
            detail["reason"] = f"the run used {used or 'no datasource'}, the case names {wanted}"
        return Score(value=1.0 if passed else 0.0, passed=passed, detail=detail)
