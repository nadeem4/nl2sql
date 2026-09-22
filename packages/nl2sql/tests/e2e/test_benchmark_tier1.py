"""Tier 1: every hand-written gold plan through the code nodes, as every demo role.

``nl2sql benchmark --tier 1`` on the generated Chinook demo, with no API key in
the environment: the command serves the gold plans from its own local fake
LLM, so the logical validator (and RBAC), the SQL generator and the executor
are what is under test. Every engine bug found on 2026-09-20 was in those
nodes. Runs in the key-free integration job on every PR.
"""
import json

import pytest

from nl2sql.evaluation.evaluator import UNANSWERABLE_SKIP_REASON
from nl2sql.evaluation.gold import load_gold_dataset

from .conftest import _base_env, run_cli


@pytest.mark.e2e
def test_tier1_passes_every_gold_plan_for_every_role(demo_project, tmp_path):
    report_path = tmp_path / "tier1.json"
    proc = run_cli(demo_project, _base_env(), "benchmark", "--tier", "1",
                   "--export-path", str(report_path), timeout=900)

    assert report_path.exists(), proc.stdout + proc.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    tier1 = report["configs"]["tier1"]
    failures = [f"{r['id']}/{r['role']}: {r['reason']}" for r in tier1["results"] if r["status"] == "fail"]
    assert not failures, "\n".join(failures)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    dataset = load_gold_dataset()
    cases = {(r["id"], r["role"]): r for r in tier1["results"]}
    assert set(cases) == {(q.id, role) for q in dataset for role in q.expected}
    for q in dataset:
        for role, expected in q.expected.items():
            case = cases[(q.id, role)]
            if expected == "unanswerable":
                assert (case["status"], case["reason"]) == ("skip", UNANSWERABLE_SKIP_REASON)
            else:
                assert case["status"] == "pass", case

    # The printed summary names each role.
    for role in ("admin", "analyst", "viewer"):
        assert role in proc.stdout
