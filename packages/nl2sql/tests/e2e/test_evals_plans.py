"""Mode plans: every hand-written gold plan through the code nodes, as every demo role, with no key.

``engine.evals.run(mode="plans")`` on the project ``nl2sql init --demo``
writes, whose ``evals.yaml`` names the Chinook suite. The run serves the
gold plans from its own local fake LLM, so the logical validator (and RBAC),
the SQL generator and the executor are what is under test. Every engine bug
found on 2026-09-20 was in those nodes. Runs in the key-free integration job
on every PR (0.6's tier 1).
"""
import pytest

from nl2sql import NL2SQL, load_config
from nl2sql.evaluation.suite import CHINOOK_SUITE_PATH, load_suite

from .conftest import PROVIDER_KEYS


@pytest.mark.e2e
def test_mode_plans_needs_no_key(demo_project, monkeypatch):
    for variable in PROVIDER_KEYS:
        monkeypatch.delenv(variable, raising=False)
    events = []
    with NL2SQL(load_config(demo_project / "nl2sql.yaml")) as engine:
        engine._runtime.bus.subscribe(events.append, names=["eval.case", "eval.finished", "run.started"])
        report = engine.evals.run(mode="plans")

    board = report.configs["plans"]
    failures = [f"{r['id']}/{r['role']}: {r['reason']}" for r in board["results"] if r["status"] != "pass"]
    assert not failures, "\n".join(failures)

    cases = load_suite(CHINOOK_SUITE_PATH).cases
    ran = {(r["id"], r["role"]) for r in board["results"]}
    # Every case runs once per role and passes, the unanswerable ones included:
    # the resolver refuses them with QUESTION_NOT_ANSWERABLE.
    assert ran == {(c.id, role) for c in cases for role in c.outcome}
    assert board["summary"]["total"]["skip"] == 0 and report.accuracy == 1.0
    assert report.curated is False and report.spent == 0.0
    # Each case ran under its own run scope, and each published its result.
    assert sum(e.name == "run.started" for e in events) == len(ran)
    assert sum(e.name == "eval.case" for e in events) == len(ran)
    assert events[-1].name == "eval.finished" and events[-1].data["accuracy"] == 1.0
