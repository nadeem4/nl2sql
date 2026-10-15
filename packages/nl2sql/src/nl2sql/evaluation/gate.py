"""The regression gate's statistics, from 0.6's tier 2, unchanged (wave_a.md, section 8, "The regression gate").

V2 builds ``engine.evals.gate`` and ``nl2sql eval run --baseline`` on these:
at most ``max_regressions`` cases that passed in the baseline and fail now, a
one-sided test at ``alpha`` on the flips, and at most ``max_cost_increase``
more cost per case. They read one config's board of an
:class:`~nl2sql.evaluation.report.EvalReport` (``report.configs[name]``).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from nl2sql.evaluation.report import reliable

# Defaults: at most two cases may flip to failing, a drop must be significant at
# p < 0.05, and cost per case may rise 20% (EvalGate in config/models.py).
DEFAULT_MAX_REGRESSIONS = 2
DEFAULT_ALPHA = 0.05
DEFAULT_MAX_COST_INCREASE = 0.20


def mcnemar_exact(pass_to_fail: int, fail_to_pass: int) -> float:
    """McNemar's exact two-sided p-value for the cases that flipped.

    The cases both runs got right, and both got wrong, carry no information
    about a change; only the discordant pairs do. Under "the two runs are
    equally good" each flip is a fair coin, so the p-value is the two-sided
    binomial tail. With no flips at all it is 1.0.
    """
    n = pass_to_fail + fail_to_pass
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(pass_to_fail, fail_to_pass) + 1))
    return min(1.0, 2 * tail / (2 ** n))


def question_flips(records: Sequence[Dict[str, Any]], baseline_records: Sequence[Dict[str, Any]]
                   ) -> Dict[str, Any]:
    """Which cases both runs ran flipped, each way, and McNemar's p-value.

    A case counts as passed when **every** pass passed, so a case that is
    flaky now and was solid before reads as a regression. Only cases both
    runs ran are compared.
    """
    now, before = reliable(records), reliable(baseline_records)
    shared = sorted(set(now) & set(before))
    pass_to_fail = [q for q in shared if before[q] and not now[q]]
    fail_to_pass = [q for q in shared if now[q] and not before[q]]
    return {"questions": len(shared), "pass_to_fail": pass_to_fail, "fail_to_pass": fail_to_pass,
            "p_value": round(mcnemar_exact(len(pass_to_fail), len(fail_to_pass)), 6)}


def compare_with_baseline(scoreboard: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per config both scoreboards name, the run against the baseline case by case."""
    out: Dict[str, Dict[str, Any]] = {}
    for name, board in scoreboard.get("configs", {}).items():
        base = baseline.get("configs", {}).get(name)
        if not base:
            continue
        out[name] = {
            **question_flips(board.get("results") or [], base.get("results") or []),
            "accuracy": board["accuracy"]["overall"], "baseline_accuracy": base["accuracy"]["overall"],
            "lenient": board["accuracy"].get("lenient"), "baseline_lenient": base["accuracy"].get("lenient"),
            "interval": board["accuracy"].get("interval"),
            "cost_per_question": board["cost"]["per_question"],
            "baseline_cost_per_question": base["cost"]["per_question"],
        }
    return out


def check_baseline(scoreboard: Dict[str, Any], baseline: Dict[str, Any], *,
                   max_regressions: int = DEFAULT_MAX_REGRESSIONS,
                   alpha: float = DEFAULT_ALPHA,
                   max_cost_increase: float = DEFAULT_MAX_COST_INCREASE) -> List[str]:
    """Each regression against ``baseline``, for every config both scoreboards name.

    A run fails when more than ``max_regressions`` cases flipped from passing
    to failing, or when the flips are one-sided enough for McNemar's exact
    test to put them below ``alpha``. Cost per case may rise by at most
    ``max_cost_increase``.
    """
    problems = []
    for name, diff in compare_with_baseline(scoreboard, baseline).items():
        lost, gained, p = len(diff["pass_to_fail"]), len(diff["fail_to_pass"]), diff["p_value"]
        if lost > max_regressions:
            problems.append(f"{name}: {lost} question{'s' if lost != 1 else ''} flipped from pass to fail "
                            f"(allowed {max_regressions}): {', '.join(diff['pass_to_fail'])}")
        elif lost > gained and p < alpha:
            problems.append(f"{name}: {lost} questions flipped to fail and {gained} the other way, "
                            f"a significant drop (McNemar p={p:.3f} < {alpha})")
        cost: Optional[float] = diff["cost_per_question"]
        base_cost: Optional[float] = diff["baseline_cost_per_question"]
        if cost is not None and base_cost and (cost - base_cost) / base_cost > max_cost_increase:
            problems.append(f"{name}: cost per question rose from ${base_cost:.4f} to ${cost:.4f} "
                            f"(allowed rise {max_cost_increase:.0%})")
    return problems
