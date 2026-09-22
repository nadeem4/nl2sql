"""Guardrail rates over recorded runs: feedback rows plus any kept run traces.

A run that has both a feedback row and a trace counts once: its signals come
from the trace, its rating from the row.
"""
from __future__ import annotations

import json

import pytest

from nl2sql.feedback import compute_stats, load_traces
from nl2sql.tracing.document import TRACE_FORMAT_VERSION


def _row(trace_id, rating, codes=(), retries=0, validator_failures=0, hits=0, sub_queries=1):
    return {"trace_id": trace_id, "rating": rating, "error_codes": list(codes), "retries": retries,
            "validator_failures": validator_failures, "plan_cache_hits": hits, "sub_queries": sub_queries}


def _trace(trace_id, codes=(), retries=0, rejected=0, sources=("llm",)):
    nodes = [{"node": "logical_validator", "status": "error", "errors": [{"error_code": "COLUMN_NOT_FOUND"}]}
             for _ in range(rejected)]
    return {
        "trace_format_version": TRACE_FORMAT_VERSION,
        "trace_id": trace_id,
        "nodes": nodes,
        "result": {
            "errors": [{"error_code": c, "severity": "ERROR"} for c in codes],
            "sub_queries": [{"retry_count": retries if i == 0 else 0, "plan_source": s, "validation": []}
                            for i, s in enumerate(sources)],
        },
    }


ROWS = [
    _row("r1", "up"),
    _row("r2", "up", hits=1),
    _row("r3", "down", codes=["SECURITY_VIOLATION"], sub_queries=0),
    _row("r4", "down", retries=2, validator_failures=1),
]
TRACES = [
    # r1 also has a trace: counted once, its signals from the trace.
    _trace("r1", sources=("cache",)),
    _trace("t1", codes=["QUESTION_NOT_ANSWERABLE"], sources=()),
    _trace("t2", codes=["DB_EXECUTION_ERROR"], retries=1, rejected=1),
    _trace("t3", sources=("llm", "cache")),
]


def test_stats_count_every_run_once():
    stats = compute_stats(ROWS, TRACES)

    assert stats["runs"] == 7
    assert stats["rated"] == 4


def test_stats_report_feedback_counts_and_rates():
    feedback = compute_stats(ROWS, TRACES)["feedback"]

    assert feedback == {"up": 2, "down": 2, "up_rate": 0.5, "down_rate": 0.5}


def test_stats_report_refusals_by_code():
    stats = compute_stats(ROWS, TRACES)

    assert stats["refusals"]["by_code"] == {"SECURITY_VIOLATION": 1, "QUESTION_NOT_ANSWERABLE": 1}
    assert stats["refusals"]["runs"] == 2
    assert stats["refusals"]["rate"] == pytest.approx(2 / 7)


def test_stats_report_retries_validator_failures_and_errors():
    stats = compute_stats(ROWS, TRACES)

    assert stats["refiner_retries"] == {"runs": 2, "retries": 3, "rate": pytest.approx(2 / 7)}
    assert stats["validator_failures"] == {"runs": 2, "failures": 2, "rate": pytest.approx(2 / 7)}
    # Refusals are reported on their own, not again as errors.
    assert stats["errors"]["by_code"] == {"DB_EXECUTION_ERROR": 1}
    assert stats["errors"]["runs"] == 1


def test_stats_report_the_plan_cache_hit_rate_per_sub_query():
    plan_cache = compute_stats(ROWS, TRACES)["plan_cache"]

    # Sub-queries: r1 1 (cache, from its trace), r2 1 (hit), r3 0, r4 1, t2 1, t3 2 (one hit).
    assert plan_cache == {"sub_queries": 6, "hits": 3, "hit_rate": 0.5}


def test_stats_over_nothing_have_no_rates():
    stats = compute_stats([], [])

    assert stats["runs"] == 0
    assert stats["feedback"] == {"up": 0, "down": 0, "up_rate": None, "down_rate": None}
    assert stats["plan_cache"]["hit_rate"] is None


def test_load_traces_reads_the_trace_directory_and_skips_what_it_cannot_read(tmp_path):
    (tmp_path / "20260921T120000000000Z_t1.json").write_text(json.dumps(_trace("t1")), encoding="utf-8")
    (tmp_path / "20260921T120001000000Z_bad.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "old.json").write_text(json.dumps({"trace_format_version": 0}), encoding="utf-8")

    assert [t["trace_id"] for t in load_traces(tmp_path)] == ["t1"]
    assert load_traces(tmp_path / "missing") == []
