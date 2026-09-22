"""Guardrail rates over recorded runs: the feedback rows plus any kept run traces.

A run is keyed by its trace id and counted once. When both a feedback row and
a trace exist, the signals come from the trace (it sees retried validator
rejections) and the rating from the row. Every rate is over all runs, except
the plan-cache hit rate, which is over planned sub-queries.
"""
from __future__ import annotations

import pathlib
from typing import Any, Dict, Iterable, List, Mapping, Optional

from nl2sql.feedback.record import REFUSAL_CODES, run_signals
from nl2sql.tracing.document import load_trace

_SIGNALS = ("error_codes", "retries", "validator_failures", "plan_cache_hits", "sub_queries")


def load_traces(directory: pathlib.Path) -> List[Dict[str, Any]]:
    """Every readable run trace directly in ``directory``; unreadable or older-format files are skipped."""
    directory = pathlib.Path(directory)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        try:
            doc = load_trace(path)
        except (ValueError, OSError):
            continue
        if isinstance(doc, dict) and doc.get("trace_id"):
            out.append(doc)
    return out


def _rate(part: int, whole: int) -> Optional[float]:
    return part / whole if whole else None


def compute_stats(rows: Iterable[Mapping[str, Any]], traces: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    runs: Dict[str, Dict[str, Any]] = {}
    for trace in traces:
        runs[str(trace["trace_id"])] = {"rating": None, **run_signals(trace.get("result") or {}, trace)}
    for row in rows:
        key = str(row.get("trace_id"))
        run = runs.get(key) or {name: row.get(name) or (0 if name != "error_codes" else []) for name in _SIGNALS}
        run["rating"] = row.get("rating")
        runs[key] = run

    total = len(runs)
    up = sum(1 for r in runs.values() if r["rating"] == "up")
    down = sum(1 for r in runs.values() if r["rating"] == "down")
    refusals = {code: 0 for code in REFUSAL_CODES}
    errors: Dict[str, int] = {}
    refused_runs = error_runs = 0
    for run in runs.values():
        codes = run["error_codes"] or []
        refused = [c for c in codes if c in REFUSAL_CODES]
        other = [c for c in codes if c not in REFUSAL_CODES]
        for code in refused:
            refusals[code] += 1
        for code in other:
            errors[code] = errors.get(code, 0) + 1
        refused_runs += bool(refused)
        error_runs += bool(other)
    retried = [r for r in runs.values() if r["retries"]]
    rejected = [r for r in runs.values() if r["validator_failures"]]
    planned = sum(int(r["sub_queries"] or 0) for r in runs.values())
    hits = sum(int(r["plan_cache_hits"] or 0) for r in runs.values())

    return {
        "runs": total,
        "rated": up + down,
        "feedback": {"up": up, "down": down, "up_rate": _rate(up, up + down), "down_rate": _rate(down, up + down)},
        "refusals": {"runs": refused_runs, "rate": _rate(refused_runs, total),
                     "by_code": {c: n for c, n in refusals.items() if n}},
        "refiner_retries": {"runs": len(retried), "retries": sum(int(r["retries"]) for r in retried),
                            "rate": _rate(len(retried), total)},
        "validator_failures": {"runs": len(rejected), "failures": sum(int(r["validator_failures"]) for r in rejected),
                               "rate": _rate(len(rejected), total)},
        "errors": {"runs": error_runs, "rate": _rate(error_runs, total),
                   "by_code": dict(sorted(errors.items(), key=lambda kv: (-kv[1], kv[0])))},
        "plan_cache": {"sub_queries": planned, "hits": hits, "hit_rate": _rate(hits, planned)},
    }
