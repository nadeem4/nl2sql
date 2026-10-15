"""The regression gate: an eval report against a baseline (wave_a.md, section 8, "The regression gate").

:func:`gate` is what ``engine.evals.gate`` and ``nl2sql eval run --baseline``
call. A baseline is another report, or a record from ``benchmarks/``
(:mod:`nl2sql.evaluation.records`), or the newest records in a
``benchmarks/`` folder (:func:`load_baseline`). Only runs of the **same
suite digest and mode** are compared; anything else is a series break and
nothing is compared.

For each config both runs have, the gate fails when:

* more than ``max_regressions`` cases passed in the baseline and fail now, or
  the flips are one-sided enough for McNemar's exact test to put them below
  ``alpha``; or
* cost per case rose by more than ``max_cost_increase``.

A case passes on **execution accuracy** (``result_match`` and ``outcome``,
:func:`~nl2sql.evaluation.report.execution_passed`): the measure 0.6's tier 2
published and gated on, so a run compares with the records published before
0.7, and a faithfulness verdict alone is never a regression. It is checked
twice: as users see it, and on the generated path (a case a verified query
answered is read by its second run, with curated answers off). Mode
retrieval has no execution, so its cases are read by their status
(``retrieval_recall`` and ``routing``).
"""
from __future__ import annotations

import json
import math
import pathlib
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from pydantic import BaseModel, Field

from nl2sql.evaluation.report import EvalReport, execution_passed, reliable, suite_identity

# Defaults: at most two cases may flip to failing, a drop must be significant at
# p < 0.05, and cost per case may rise 20% (EvalGate in config/models.py).
DEFAULT_MAX_REGRESSIONS = 2
DEFAULT_ALPHA = 0.05
DEFAULT_MAX_COST_INCREASE = 0.20

Passed = Callable[[Dict[str, Any]], bool]
Baseline = Union[EvalReport, Dict[str, Any], Sequence[Dict[str, Any]]]


class GateResult(BaseModel):
    """What the gate found. ``ok`` only when the runs were compared and nothing regressed.

    ``series_break`` says why nothing (or not every config) was compared: another
    suite, another mode, or no config in common. ``compared`` holds, per config,
    the flips and accuracies of each measure (``accuracy``, and ``generated``
    unless the mode is retrieval) and both costs per case.
    """

    ok: bool
    regressions: List[str] = Field(default_factory=list)
    series_break: Optional[str] = None
    compared: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


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


def _status(record: Dict[str, Any]) -> bool:
    return record["status"] == "pass"


def _generated(record: Dict[str, Any]) -> bool:
    return execution_passed(record.get("generated") or record)


def question_flips(records: Sequence[Dict[str, Any]], baseline_records: Sequence[Dict[str, Any]],
                   passed: Passed = execution_passed) -> Dict[str, Any]:
    """Which cases both runs ran flipped, each way, and McNemar's p-value.

    A case counts as passed when **every** pass passed (``passed`` says
    whether one run did), so a case that is flaky now and was solid before
    reads as a regression. Only cases both runs ran are compared.
    """
    now, before = reliable(records, passed=passed), reliable(baseline_records, passed=passed)
    shared = sorted(set(now) & set(before))
    pass_to_fail = [q for q in shared if before[q] and not now[q]]
    fail_to_pass = [q for q in shared if now[q] and not before[q]]
    return {"questions": len(shared), "pass_to_fail": pass_to_fail, "fail_to_pass": fail_to_pass,
            "p_value": round(mcnemar_exact(len(pass_to_fail), len(fail_to_pass)), 6)}


def _rate(records: Sequence[Dict[str, Any]], passed: Passed) -> Optional[float]:
    return round(sum(passed(r) for r in records) / len(records), 4) if records else None


def compare_with_baseline(scoreboard: Dict[str, Any], baseline: Dict[str, Any], passed: Passed = execution_passed
                          ) -> Dict[str, Dict[str, Any]]:
    """Per config both scoreboards name (``{"configs": {name: board}}``), the run against the baseline case by case."""
    out: Dict[str, Dict[str, Any]] = {}
    for name, board in scoreboard.get("configs", {}).items():
        base = baseline.get("configs", {}).get(name)
        if not base:
            continue
        now_records, base_records = board.get("results") or [], base.get("results") or []
        out[name] = {
            **question_flips(now_records, base_records, passed),
            "accuracy": _rate(now_records, passed), "baseline_accuracy": _rate(base_records, passed),
            "cost_per_question": board["cost"]["per_question"],
            "baseline_cost_per_question": base["cost"]["per_question"],
        }
    return out


def _flip_problems(name: str, diff: Dict[str, Any], max_regressions: int, alpha: float) -> List[str]:
    lost, gained, p = len(diff["pass_to_fail"]), len(diff["fail_to_pass"]), diff["p_value"]
    if lost > max_regressions:
        return [f"{name}: {lost} question{'s' if lost != 1 else ''} flipped from pass to fail "
                f"(allowed {max_regressions}): {', '.join(diff['pass_to_fail'])}"]
    if lost > gained and p < alpha:
        return [f"{name}: {lost} questions flipped to fail and {gained} the other way, "
                f"a significant drop (McNemar p={p:.3f} < {alpha})"]
    return []


def _cost_problem(name: str, diff: Dict[str, Any], max_cost_increase: float) -> List[str]:
    cost: Optional[float] = diff["cost_per_question"]
    base_cost: Optional[float] = diff["baseline_cost_per_question"]
    if cost is not None and base_cost and (cost - base_cost) / base_cost > max_cost_increase:
        return [f"{name}: cost per question rose from ${base_cost:.4f} to ${cost:.4f} "
                f"(allowed rise {max_cost_increase:.0%})"]
    return []


def check_baseline(scoreboard: Dict[str, Any], baseline: Dict[str, Any], *,
                   max_regressions: int = DEFAULT_MAX_REGRESSIONS,
                   alpha: float = DEFAULT_ALPHA,
                   max_cost_increase: float = DEFAULT_MAX_COST_INCREASE,
                   passed: Passed = execution_passed) -> List[str]:
    """Each regression against ``baseline``, for every config both scoreboards name, on one measure."""
    problems = []
    for name, diff in compare_with_baseline(scoreboard, baseline, passed).items():
        problems += _flip_problems(name, diff, max_regressions, alpha)
        problems += _cost_problem(name, diff, max_cost_increase)
    return problems


# ---------------------------------------------------------------- the gate

class _Side(BaseModel):
    """One config's board, with the mode and suite digest it ran."""

    mode: str
    digest: str
    name: str
    board: Dict[str, Any]


def _sides(run: Baseline) -> List[_Side]:
    """Each config of a report, or each record, as a side the gate can compare."""
    from nl2sql.evaluation.records import mode_of

    if isinstance(run, EvalReport):
        run = run.model_dump(mode="json")
    if isinstance(run, dict) and "kind" in run:
        run = [run]
    if isinstance(run, dict):
        digest = suite_identity(run)["sha256"]
        return [_Side(mode=run["mode"], digest=digest, name=name, board=board)
                for name, board in run.get("configs", {}).items()]
    return [_Side(mode=mode_of(r), digest=r["dataset"]["sha256"], name=r["config"]["name"],
                  board=r.get("scoreboard") or {}) for r in run]


def gate(report: Union[EvalReport, Dict[str, Any]], baseline: Baseline, *,
         max_regressions: int = DEFAULT_MAX_REGRESSIONS, alpha: float = DEFAULT_ALPHA,
         max_cost_increase: float = DEFAULT_MAX_COST_INCREASE) -> GateResult:
    """``report`` against ``baseline``: a report, one record, or a list of records (one per config).

    See the module docstring for the rules. Every config of ``report`` that the
    baseline also has is compared, provided both ran the same suite digest in
    the same mode.
    """
    breaks: List[str] = []
    problems: List[str] = []
    compared: Dict[str, Dict[str, Any]] = {}
    base_sides = {side.name: side for side in _sides(baseline)}
    for now in _sides(report):
        base = base_sides.get(now.name)
        if base is None:
            continue
        if base.mode != now.mode:
            breaks.append(f"{now.name}: the baseline ran mode {base.mode}, this run mode {now.mode}")
            continue
        if base.digest != now.digest:
            breaks.append(f"{now.name}: the baseline ran another suite (@{base.digest[:8]}, "
                          f"this run @{now.digest[:8]})")
            continue
        measures: Dict[str, Passed] = ({"accuracy": _status} if now.mode == "retrieval"
                                       else {"accuracy": execution_passed, "generated": _generated})
        compared[now.name] = {}
        for measure, passed in measures.items():
            [diff] = compare_with_baseline({"configs": {now.name: now.board}},
                                           {"configs": {now.name: base.board}}, passed).values()
            compared[now.name][measure] = diff
            label = now.name if measure == "accuracy" else f"{now.name} (generated)"
            problems += _flip_problems(label, diff, max_regressions, alpha)
        problems += _cost_problem(now.name, compared[now.name]["accuracy"], max_cost_increase)
    if not compared and not breaks:
        mine = ", ".join(s.name for s in _sides(report)) or "none"
        theirs = ", ".join(base_sides) or "none"
        breaks.append(f"no config in common with the baseline (this run: {mine}; the baseline: {theirs})")
    series_break = "; ".join(breaks) or None
    return GateResult(ok=not problems and series_break is None, regressions=problems,
                      series_break=series_break, compared=compared)


def load_baseline(path: Union[str, pathlib.Path], report: Union[EvalReport, Dict[str, Any]]
                  ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """The baseline at ``path``: a report or record file, or the newest records in a ``benchmarks/`` folder.

    From a folder, for each config of ``report``, the newest record of the
    report's mode, on the report's database, with that config's name. An
    empty list when there is none; the gate then says nothing was compared.
    """
    from nl2sql.evaluation.records import load_records, mode_of

    path = pathlib.Path(path)
    if not path.is_dir():
        return json.loads(path.read_text(encoding="utf-8"))
    data = report.model_dump(mode="json") if isinstance(report, EvalReport) else report
    database = (data.get("database") or {}).get("datasource_id")
    recorded = load_records(path)
    chosen = []
    for name in data.get("configs", {}):
        newest = next((r for r in recorded
                       if mode_of(r) == data["mode"] and r["config"]["name"] == name
                       and (database is None or r["database"]["datasource_id"] == database)), None)
        if newest is not None:
            chosen.append(newest)
    return chosen
