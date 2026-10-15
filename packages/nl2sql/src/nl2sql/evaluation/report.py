"""An eval run's report: one board per LLM config, scored from its case records (wave_a.md, section 8).

Each case run is a record (a dict, written as JSON): the case and role, its
``status`` (``pass`` when every scorer that applies passed, ``fail``,
``error`` when a scorer raised, ``skip`` when none applied), each scorer's
``scores``, the answer's ``source``, cost, latency, tokens and the SQL. With
curated answers on, a case a verified query answered also has ``generated``:
the same case run again with curated answers off.

A board (:func:`score_config`) reads the records: ``accuracy`` (what users
see), ``accuracy.generated`` (every case on the generated path: the model's
own skill, comparable across configs), both by source, tag and difficulty,
with a 95% Wilson interval and, over several passes, pass^k. Then
answerability, cost, latency, tokens, retries, determinism, faithfulness, and
each scorer's counts.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

OUTCOMES = ("pass", "fail", "error", "skip")

_TOKEN_FIELDS = ("calls", "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                 "output_tokens", "reasoning_tokens")

# 1.96 is the two-sided 95% normal quantile.
Z_95 = 1.959963984540054


class EvalReport(BaseModel):
    """What ``engine.evals.run`` returns. ``configs`` maps each LLM config's name to its board."""

    mode: str
    suites: List[Dict[str, str]] = Field(default_factory=list)  # name, path, digest
    roles: Optional[List[str]] = None
    passes: int = 1
    curated: bool = False
    clarify: bool = False
    max_cost_usd: Optional[float] = None
    spent: float = 0.0
    stopped: Optional[str] = None  # "max_cost" when the cap stopped the run
    skipped: int = 0  # role runs left out: a clarify case, with clarify off
    prices_checked_on: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    configs: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    comparison: Optional[Dict[str, Any]] = None
    database: Optional[Dict[str, Any]] = None

    @property
    def accuracy(self) -> Optional[float]:
        """The first config's accuracy: what users see. With several configs, read ``configs[name]``."""
        board = next(iter(self.configs.values()), None)
        return board["accuracy"]["overall"] if board else None

    @property
    def accuracy_generated(self) -> Optional[float]:
        """The first config's accuracy with every case on the generated path."""
        board = next(iter(self.configs.values()), None)
        return board["accuracy"]["generated"] if board else None


def case_status(scores: Dict[str, Dict[str, Any]], lenient: bool = False) -> Tuple[str, str]:
    """``(status, reason)`` from each scorer's score.

    ``error`` when a scorer raised; else ``pass`` when every scorer that
    applies passed, ``fail`` with the first failure's reason, ``skip`` when
    none applied. ``lenient`` reads ``result_match`` by its lenient verdict.
    """
    applied = {n: s for n, s in scores.items() if s.get("passed") is not None or "error" in s.get("detail", {})}
    errors = [f"{n}: {s['detail']['error']}" for n, s in applied.items() if "error" in s.get("detail", {})]
    if errors:
        return "error", errors[0]
    if not applied:
        return "skip", ""
    for name, score in applied.items():
        passed = score["detail"].get("lenient", score["passed"]) if lenient and name == "result_match" \
            else score["passed"]
        if not passed:
            return "fail", f"{name}: {score['detail'].get('reason', 'failed')}"
    return "pass", ""


def summarize(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts each outcome per role and in total."""
    def empty() -> Dict[str, int]:
        return {outcome: 0 for outcome in OUTCOMES}

    by_role: Dict[str, Dict[str, int]] = {}
    total = empty()
    for r in records:
        by_role.setdefault(r["role"], empty())[r["status"]] += 1
        total[r["status"]] += 1
    return {"by_role": by_role, "total": total}


def _percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile ``q`` in [0, 1]; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo), 4)


def _share(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def wilson_interval(successes: int, total: int, z: float = Z_95) -> Optional[Tuple[float, float]]:
    """The Wilson score interval for ``successes`` of ``total``; None for no runs.

    A point estimate hides how little 47 cases can settle: 27/47 is 57.4%
    with a 95% interval of 43.3%-70.5%, so a few points either way is noise.
    Wilson rather than the normal approximation because it stays inside
    [0, 1] and behaves at 0 and 100%
    (https://www.anthropic.com/research/statistical-approach-to-model-evals).
    """
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def _interval(successes: int, total: int) -> Optional[List[float]]:
    bounds = wilson_interval(successes, total)
    return None if bounds is None else [round(b, 4) for b in bounds]


def _lenient(record: Dict[str, Any]) -> str:
    return record.get("lenient_status") or record["status"]


def _generated(record: Dict[str, Any]) -> str:
    """The case's status on the generated path: its second run when a verified query answered the first."""
    return (record.get("generated") or {}).get("status") or record["status"]


def _pass_rate(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    passed = sum(r["status"] == "pass" for r in records)
    lenient = sum(_lenient(r) == "pass" for r in records)
    return {"pass": passed, "lenient_pass": lenient, "total": len(records),
            "accuracy": _share(passed, len(records)), "lenient_accuracy": _share(lenient, len(records))}


def _execution(record: Dict[str, Any]) -> bool:
    """The 0.6 tier 2 measure: ``result_match`` and ``outcome`` passed where they apply; no other scorer counts."""
    scores = record.get("scores") or {}
    mine = {n: s for n, s in scores.items() if n in ("result_match", "outcome")}
    if any("error" in s.get("detail", {}) for s in mine.values()):
        return False
    applied = [s for s in mine.values() if s.get("passed") is not None]
    return bool(applied) and all(s["passed"] for s in applied)


def _generated_rate(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    passed = sum(_generated(r) == "pass" for r in records)
    return {"pass": passed, "total": len(records), "accuracy": _share(passed, len(records))}


def _key(record: Dict[str, Any]) -> str:
    return f"{record['id']}/{record['role']}"


def _by_case(records: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    runs: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        runs.setdefault(_key(r), []).append(r)
    return runs


def reliable(records: Sequence[Dict[str, Any]], lenient: bool = False) -> Dict[str, bool]:
    """Per case and role, whether **every** pass passed: the pass^k rule."""
    status = _lenient if lenient else (lambda r: r["status"])
    return {q: all(status(r) == "pass" for r in runs) for q, runs in _by_case(records).items()}


def _pass_k(records: Sequence[Dict[str, Any]], passes: int) -> Optional[Dict[str, Any]]:
    """pass^k: the share of cases that passed in every one of ``passes`` runs.

    Mean accuracy counts runs, so a case that passes twice of three times
    lifts it; pass^k counts cases and a flaky one never counts (tau-bench,
    https://arxiv.org/abs/2406.12045). Reported only over more than one pass.
    """
    if passes <= 1:
        return None
    strict, lenient = reliable(records), reliable(records, lenient=True)
    return {"k": passes, "questions": len(strict),
            "strict": _share(sum(strict.values()), len(strict)),
            "lenient": _share(sum(lenient.values()), len(lenient))}


def _scorer_counts(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per scorer: how many cases it applied to, passed, failed and errored on, and its mean value."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in records:
        for name, score in (r.get("scores") or {}).items():
            counts = out.setdefault(name, {"applied": 0, "passed": 0, "failed": 0, "errors": 0, "values": []})
            if "error" in score.get("detail", {}):
                counts["applied"] += 1
                counts["errors"] += 1
            elif score.get("passed") is not None:
                counts["applied"] += 1
                counts["passed" if score["passed"] else "failed"] += 1
                if score.get("value") is not None:
                    counts["values"].append(score["value"])
    return {name: {**{k: v for k, v in c.items() if k != "values"}, "mean": _mean(c["values"])}
            for name, c in sorted(out.items())}


def _retrieval(records: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Mean table and column recall and how much was sent, over the cases ``retrieval_recall`` scored."""
    details = [s["detail"] for r in records for n, s in (r.get("scores") or {}).items()
               if n == "retrieval_recall" and s.get("passed") is not None]
    if not details:
        return None
    return {"cases": len(details),
            "table_recall": _mean([d["table_recall"] for d in details if d["table_recall"] is not None]),
            "column_recall": _mean([d["column_recall"] for d in details if d["column_recall"] is not None]),
            "perfect_tables": sum(d["table_recall"] == 1.0 for d in details),
            "perfect_columns": sum(d["column_recall"] == 1.0 for d in details),
            "tables_sent": _mean([d["tables_sent"] for d in details]),
            "columns_sent": _mean([d["columns_sent"] for d in details])}


def score_config(records: List[Dict[str, Any]], passes: int) -> Dict[str, Any]:
    """The board for one config's runs (every pass, every case and role)."""
    by_tag: Dict[str, List] = {}
    by_difficulty: Dict[str, List] = {}
    by_source: Dict[str, List] = {}
    for r in records:
        for tag in r.get("tags") or []:
            by_tag.setdefault(tag, []).append(r)
        by_difficulty.setdefault(r.get("difficulty") or "unknown", []).append(r)
        # A record written before sources were kept, or a case that raised, has none.
        by_source.setdefault(r.get("source") or "unknown", []).append(r)

    unanswerable = [r for r in records if r["expected"] == "unanswerable"]
    true_refusals = sum(r.get("refused_unanswerable", False) for r in unanswerable)
    false_refusals = sum(r.get("refused_unanswerable", False) for r in records if r["expected"] != "unanswerable")

    tokens: Dict[str, Dict[str, int]] = {}
    for r in records:
        for node, counts in (r.get("tokens_by_node") or {}).items():
            totals = tokens.setdefault(node, {f: 0 for f in _TOKEN_FIELDS})
            for f in _TOKEN_FIELDS:
                totals[f] += counts.get(f, 0)

    node_times: Dict[str, List[float]] = {}
    for r in records:
        for node, seconds in (r.get("timings") or {}).items():
            node_times.setdefault(node, []).append(seconds)

    errors: Dict[str, int] = {}
    for r in records:
        for code in r.get("error_codes") or []:
            errors[code] = errors.get(code, 0) + 1

    determinism = None
    if passes > 1:
        runs: Dict[str, set] = {}
        for r in records:
            runs.setdefault(_key(r), set()).add((r.get("sql"), r.get("rows_digest")))
        differing = sorted(k for k, seen in runs.items() if len(seen) > 1)
        determinism = {"identical": len(runs) - len(differing), "questions": len(runs),
                       "share": _share(len(runs) - len(differing), len(runs)), "differing": differing}

    # Whether the written answer's numbers and names come from the rows, over
    # every run that wrote an answer.
    answered = [r for r in records if r.get("faithfulness")]
    faithful = sum(r["faithfulness"]["faithful"] for r in answered)
    faithfulness = {
        "faithful": faithful, "answers": len(answered), "rate": _share(faithful, len(answered)),
        "unfaithful": [{"id": r["id"], "role": r["role"], "pass": r["pass"],
                        "unsupported_numbers": r["faithfulness"]["unsupported_numbers"],
                        "unsupported_entities": r["faithfulness"]["unsupported_entities"]}
                       for r in answered if not r["faithfulness"]["faithful"]],
    }

    total_cost = sum(r.get("cost") or 0.0 for r in records)
    overall = _pass_rate(records)
    generated = _generated_rate(records)
    executed = sum(_execution(r) for r in records)
    latencies = [r["latency_s"] for r in records if r.get("latency_s") is not None]
    return {
        "summary": summarize(records),
        "accuracy": {
            # Strict: execution match, column count and order included. Lenient
            # beside it allows extra and reordered columns and period labels.
            "overall": overall["accuracy"],
            "lenient": overall["lenient_accuracy"],
            "generated": generated["accuracy"],
            # Execution accuracy alone: rows and outcome, as the 0.6 tier 2
            # scored it, so the published series stays comparable.
            "execution": _share(executed, len(records)),
            # A 95% Wilson interval on each: at n = 47 it is about +-14 points.
            # Over several passes the runs of one case are not independent, so
            # it reads narrower than it is; pass^k counts cases and does not.
            "interval": _interval(overall["pass"], overall["total"]),
            "lenient_interval": _interval(overall["lenient_pass"], overall["total"]),
            "generated_interval": _interval(generated["pass"], generated["total"]),
            "execution_interval": _interval(executed, len(records)),
            "pass_k": _pass_k(records, passes),
            "by_tag": {t: _pass_rate(rs) for t, rs in sorted(by_tag.items())},
            "by_difficulty": {d: _pass_rate(rs) for d, rs in sorted(by_difficulty.items())},
            "by_source": {s: _pass_rate(rs) for s, rs in sorted(by_source.items())},
            "generated_by_source": {s: _generated_rate(rs) for s, rs in sorted(by_source.items())},
        },
        "answerability": {
            "true_refusals": true_refusals, "false_refusals": false_refusals,
            "missed_unanswerable": len(unanswerable) - true_refusals,
            "precision": _share(true_refusals, true_refusals + false_refusals),
            "recall": _share(true_refusals, len(unanswerable)),
        },
        "scores": _scorer_counts(records),
        "retrieval": _retrieval(records),
        "tokens_by_node": tokens,
        "cost": {"total": round(total_cost, 6),
                 "per_question": round(total_cost / len(records), 6) if records else None},
        "latency": {
            "question": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
            "by_node": {n: {"p50": _percentile(v, 0.5), "p95": _percentile(v, 0.95)}
                        for n, v in sorted(node_times.items())},
        },
        "retries": {"total": sum(r.get("retries", 0) for r in records),
                    "questions_with_retries": sum(r.get("retries", 0) > 0 for r in records)},
        "errors_by_code": errors,
        "determinism": determinism,
        "faithfulness": faithfulness,
        "results": records,
    }


def _outcome(statuses: List[str]) -> str:
    if all(s == "pass" for s in statuses):
        return "pass"
    return "fail" if not any(s == "pass" for s in statuses) else "flaky"


def compare(boards: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Configs side by side, and each case and role at least two configs scored differently."""
    rows = [{"config": name, "cases": len(b["results"]), "accuracy": b["accuracy"]["overall"],
             "accuracy_generated": b["accuracy"]["generated"],
             "execution_accuracy": b["accuracy"]["execution"],
             "lenient_accuracy": b["accuracy"]["lenient"],
             "answerability_precision": b["answerability"]["precision"],
             "answerability_recall": b["answerability"]["recall"],
             "cost_total": b["cost"]["total"], "cost_per_question": b["cost"]["per_question"],
             "latency_p50": b["latency"]["question"]["p50"], "latency_p95": b["latency"]["question"]["p95"],
             "retries": b["retries"]["total"],
             "determinism": (b["determinism"] or {}).get("share"),
             "faithfulness": (b.get("faithfulness") or {}).get("rate")} for name, b in boards.items()]

    outcomes: Dict[tuple, Dict[str, List[str]]] = {}
    for name, b in boards.items():
        for r in b["results"]:
            outcomes.setdefault((r["id"], r["role"]), {}).setdefault(name, []).append(r["status"])
    differences = []
    for (qid, role), per_config in sorted(outcomes.items()):
        summary = {name: _outcome(statuses) for name, statuses in per_config.items()}
        if len(summary) > 1 and len(set(summary.values())) > 1:
            differences.append({"id": qid, "role": role, "outcomes": summary})
    return {"configs": rows, "differences": differences}
