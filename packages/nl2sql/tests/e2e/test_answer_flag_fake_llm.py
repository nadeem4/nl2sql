"""``answer=False``: a run that stops at the aggregator and writes no prose (wave_c.md, D119).

A dashboard refresh re-runs stored plans with ``run_query(plan=..., answer=False)``: no model call at all,
``final_answer`` is None, and the rows are the same.
"""
from __future__ import annotations

import pytest

from .recordings_chinook import RULES_COUNT_CUSTOMERS
from .test_approval_fake_llm import COUNT, _supplied
from .test_result_paging_fake_llm import _env


@pytest.fixture
def engine(demo_project, fake_llm, monkeypatch):
    from nl2sql import NL2SQL

    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
    _env(monkeypatch, demo_project, env)
    built = NL2SQL.from_config()
    yield built, server
    built.close()


def _analyst():
    from nl2sql import UserContext

    return UserContext(roles=["analyst"])


@pytest.mark.e2e
def test_answer_false_runs_a_supplied_plan_with_no_model_call(engine):
    engine, server = engine

    result = engine.run_query(COUNT, user_context=_analyst(), plan=_supplied(), answer=False)

    assert result.status == "success", result.errors
    assert result.final_answer is None
    assert result.sub_queries[0].rows.rows == [[59]]
    assert "answer_synthesizer" not in result.timings
    assert server.calls == [], "not one model call"


@pytest.mark.e2e
def test_the_default_still_writes_the_answer(engine):
    engine, server = engine

    result = engine.run_query(COUNT, user_context=_analyst(), plan=_supplied())

    assert result.final_answer is not None
    assert [c["name"] for c in server.calls] == ["AggregatedResponse"]


@pytest.mark.e2e
def test_answer_false_on_the_stream_writes_no_answer(engine):
    engine, server = engine

    events = list(engine.stream_query(COUNT, user_context=_analyst(), plan=_supplied(), answer=False))

    assert events[-1].name == "run.finished"
    assert events[-1].data["result"]["final_answer"] is None
    assert not [e for e in events if e.name == "answer.delta"]
    assert server.calls == []
