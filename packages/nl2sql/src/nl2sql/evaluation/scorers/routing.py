"""``routing``: the question went to the case's ``datasources``.

Mode ``full``: the datasources the run's sub-queries used are exactly the
case's. Mode ``retrieval``: routing ranked one of the case's datasources
first (``top1``), and every one of them is among those it ranked, which the
runner cuts at ``settings.routing.max_datasources`` (``shortlist``); the case
passes on both, so the gate fails when either drops. Applies to a role whose
outcome is ``allowed``.
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
            routed = list(run.routed)
            missed = [d for d in wanted if d not in routed]
            top1 = bool(routed) and routed[0] in wanted
            detail = {"routed": routed, "missed": missed, "top1": top1, "shortlist": not missed}
            reasons = []
            if not top1:
                reasons.append(f"routing ranked {routed[0]} first" if routed else "routing ranked nothing")
            if missed:
                reasons.append(f"the shortlist {routed} misses {missed}")
            if reasons:
                detail["reason"] = "; ".join(reasons)
            passed = top1 and not missed
            return Score(value=1.0 if passed else 0.0, passed=passed, detail=detail)
        used = sorted({sq.datasource_id for sq in run.result.sub_queries if sq.datasource_id})
        passed = used == wanted
        detail = {"used": used}
        if not passed:
            detail["reason"] = f"the run used {used or 'no datasource'}, the case names {wanted}"
        return Score(value=1.0 if passed else 0.0, passed=passed, detail=detail)
