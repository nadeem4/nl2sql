"""``engine.evals.gate``: a report against a baseline report or record (wave_a.md, section 8, "The regression gate").

The gate compares only runs of the same suite digest and mode, and it reads
**execution accuracy** (``result_match`` and ``outcome``, the measure 0.6's
tier 2 published), never ``overall``: a faithfulness miss is not a regression.
It checks that accuracy and, separately, the generated path's.
"""
import json

from nl2sql.evaluation import gate, records, report
from nl2sql.evaluation.report import EvalReport

DIGEST = "a" * 64
CHINOOK = {"datasource_id": "chinook", "engine": "sqlite", "schema_fingerprint": "f" * 16,
           "tables": 11, "columns": 64}


def _case(qid, *, rows=True, outcome=True, faithful=True, generated=None, cost=0.01):
    """One case record as run.py writes it: each scorer's verdict, and the generated run's when there was one."""
    scores = {"result_match": {"passed": rows, "value": None, "detail": {}},
              "outcome": {"passed": outcome, "value": None, "detail": {}},
              "faithfulness": {"passed": faithful, "value": None, "detail": {}}}
    status, reason = report.case_status(scores)
    rec = {"id": qid, "role": "admin", "pass": 1, "expected": "allowed", "status": status, "reason": reason,
           "lenient_status": status, "scores": scores, "tags": [], "difficulty": "easy", "cost": cost,
           "latency_s": 1.0, "sql": "SELECT 1", "rows_digest": "d", "refused_unanswerable": False, "retries": 0,
           "error_codes": [], "tokens_by_node": {}, "timings": {}, "source": "generated", "generated": None}
    if generated is not None:
        g_scores = {**scores, "result_match": {"passed": generated, "value": None, "detail": {}}}
        g_status, g_reason = report.case_status(g_scores)
        rec.update(source="verified", generated={"status": g_status, "reason": g_reason, "lenient_status": g_status,
                                                 "scores": g_scores, "sql": "SELECT 2"})
    return rec


def _report(cases, *, mode="full", digest=DIGEST, name="gpt-5.4", curated=False):
    board = report.score_config(cases, passes=1)
    board.update(planned_cases=len(cases), completed_cases=len(cases), models={"ast_planner": "openai:gpt-5.4"})
    return EvalReport(mode=mode, suites=[{"name": "chinook", "path": "evals/chinook.yaml", "digest": digest}],
                      curated=curated, configs={name: board}, database=CHINOOK)


def _passing(n=10):
    return [_case(f"q{i}") for i in range(n)]


def test_gate_compares_only_the_same_suite_and_mode():
    base = _report(_passing())
    worse = _report([_case(f"q{i}", rows=False) for i in range(10)])

    assert not gate.gate(worse, base).ok  # same suite and mode: compared, and it regressed

    other_suite = gate.gate(worse, _report(_passing(), digest="b" * 64))
    assert not other_suite.ok and other_suite.regressions == []
    assert "suite" in other_suite.series_break

    other_mode = gate.gate(_report(_passing(), mode="plans", name="plans"), _report(_passing(), name="plans"))
    assert not other_mode.ok and "mode" in other_mode.series_break

    unrelated = gate.gate(worse, _report(_passing(), name="claude"))
    assert not unrelated.ok and "no config" in unrelated.series_break


def test_gate_checks_both_accuracies():
    # What users see is unchanged (a verified query answers), but the model alone lost three cases.
    base = _report([_case(f"q{i}", generated=True) for i in range(10)], curated=True)
    now = _report([_case(f"q{i}", generated=i >= 3) for i in range(10)], curated=True)

    result = gate.gate(now, base)

    assert not result.ok and len(result.regressions) == 1
    assert result.regressions[0].startswith("gpt-5.4 (generated):")
    assert result.compared["gpt-5.4"]["accuracy"]["pass_to_fail"] == []
    assert result.compared["gpt-5.4"]["generated"]["pass_to_fail"] == ["q0/admin", "q1/admin", "q2/admin"]

    users = _report([_case(f"q{i}", rows=i >= 3, generated=True) for i in range(10)], curated=True)
    assert gate.gate(users, base).regressions[0].startswith("gpt-5.4:")


def test_gate_reads_execution_accuracy_not_overall():
    # Every case loses its faithfulness verdict: overall drops to 0, execution accuracy stays at 100%.
    now = _report([_case(f"q{i}", faithful=False) for i in range(10)])
    result = gate.gate(now, _report(_passing()))
    assert result.ok and result.regressions == [] and result.series_break is None
    assert result.compared["gpt-5.4"]["accuracy"]["accuracy"] == 1.0


def test_gate_checks_the_cost_per_case():
    now = _report([_case(f"q{i}", cost=0.02) for i in range(10)])
    result = gate.gate(now, _report(_passing()), max_cost_increase=0.2)
    assert not result.ok and "cost per question rose" in result.regressions[0]


def test_gate_takes_a_record_or_a_benchmarks_folder_as_the_baseline(tmp_path):
    base = _report(_passing())
    worse = _report([_case(f"q{i}", rows=i >= 4) for i in range(10)])
    when = records.dt.datetime(2026, 10, 15, tzinfo=records.dt.timezone.utc)
    code = {"git_commit": "1" * 40, "git_dirty": False}
    [path] = records.write_records(base, tmp_path, recorded_at=when, engine_version="0.7.0", code=code)
    record = json.loads(path.read_text(encoding="utf-8"))

    assert not gate.gate(worse, record).ok
    assert gate.gate(base, record).ok

    # A folder: the newest record of the report's mode, database and config.
    newer = _report([_case(f"q{i}", rows=i >= 4) for i in range(10)])
    records.write_records(newer, tmp_path, recorded_at=when.replace(day=16), engine_version="0.7.0", code=code)
    [latest] = gate.load_baseline(tmp_path, worse)
    assert latest["recorded_at"].startswith("2026-10-16")
    assert gate.gate(worse, gate.load_baseline(tmp_path, worse)).ok
    assert gate.load_baseline(tmp_path, _report(_passing(), mode="plans", name="plans")) == []


def test_a_0_6_tier_2_record_is_read_by_its_status_which_was_execution_accuracy():
    # 0.6's tier 2 wrote no scores: its status was the execution match.
    old = {"id": "q0", "role": "admin", "status": "pass", "lenient_status": "pass"}
    assert report.execution_passed(old) is True
    assert report.execution_passed({**old, "status": "fail", "lenient_status": "pass"}) is False
    assert report.execution_passed({**old, "status": "fail", "lenient_status": "pass"}, lenient=True) is True
