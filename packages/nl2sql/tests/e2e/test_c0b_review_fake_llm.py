"""The review fixes of C0b (wave_c.md, D119 and D132), end to end on the demo databases and the fake LLM.

``answer=False`` still runs ``answer.after`` (a deny withholds the rows and the manifest); a supplied verified
plan never falls back to a model run; a supplied multi-sub-query decomposition and a supplied verified plan run
end to end with zero model calls.
"""
from __future__ import annotations

import pytest
import yaml

from nl2sql.hooks import Allow, Deny

from .recordings_chinook import RULES_COUNT_CUSTOMERS
from .test_approval_fake_llm import COUNT, _supplied
from .test_curated_fake_llm import CURATED
from .test_multi_sub_query_fake_llm import (
    MUSIC_SPEND_PLAN, MUSIC_SPEND_SQ, OPEN_TICKETS_PLAN, OPEN_TICKETS_SQ, _join_decomposition,
)


class AtAnswer:
    """A hook at ``answer.after`` that denies, or allows."""

    name = "gate"
    points = frozenset({"answer.after"})

    def __init__(self, deny: bool):
        self.deny = deny

    def __call__(self, ctx):
        return Deny(reason="Not this answer") if self.deny else Allow()


def _analyst():
    from nl2sql import UserContext

    return UserContext(roles=["analyst"])


def _engine(fake_llm, demo_project, monkeypatch, *, hooks=(), curated=None):
    from nl2sql import NL2SQL
    from nl2sql.config import load_config

    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
    path = demo_project / "fake.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if curated:
        document["curated"] = curated
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = load_config(path)
    if hooks:
        config = config.model_copy(update={"hooks": list(hooks)})
    return NL2SQL(config), server


@pytest.fixture
def make(demo_project, fake_llm, monkeypatch):
    engines = []

    def _make(**kwargs):
        engine, server = _engine(fake_llm, demo_project, monkeypatch, **kwargs)
        engines.append(engine)
        return engine, server

    yield _make
    for engine in engines:
        engine.close()


# ------------------------------------------------ answer=False still runs answer.after (findings 2 and 3)


@pytest.mark.e2e
def test_a_deny_at_answer_after_withholds_with_answer_false(make):
    engine, server = make(hooks=[AtAnswer(deny=True)])

    result = engine.run_query(COUNT, user_context=_analyst(), plan=_supplied(), answer=False)

    assert result.status == "error"
    assert "HOOK_DENIED" in [e["error_code"] for e in result.errors]
    assert all(sq.rows is None for sq in result.sub_queries), "the rows are withheld with the answer"
    assert result.result_manifest == [] and result.final_answer is None
    assert server.calls == []


@pytest.mark.e2e
def test_an_allow_at_answer_after_with_answer_false_returns_the_rows(make):
    engine, server = make(hooks=[AtAnswer(deny=False)])

    result = engine.run_query(COUNT, user_context=_analyst(), plan=_supplied(), answer=False)

    assert result.status == "success", result.errors
    assert result.final_answer is None and result.sub_queries[0].rows.rows == [[59]]
    assert server.calls == []


@pytest.mark.e2e
def test_the_stream_withholds_the_manifest_on_a_deny_with_answer_false(make):
    engine, server = make(hooks=[AtAnswer(deny=True)])

    events = list(engine.stream_query(COUNT, user_context=_analyst(), plan=_supplied(), answer=False))

    assert not [e for e in events if e.name == "result.manifest"]
    assert events[-1].name == "run.finished" and events[-1].data["result"]["status"] == "error"
    assert server.calls == []


@pytest.mark.e2e
def test_the_stream_releases_the_manifest_on_an_allow_with_answer_false(make):
    engine, server = make(hooks=[AtAnswer(deny=False)])

    events = list(engine.stream_query(COUNT, user_context=_analyst(), plan=_supplied(), answer=False))

    assert [e.name for e in events if e.name == "result.manifest"] == ["result.manifest"]
    assert events[-1].data["result"]["status"] == "success"
    assert server.calls == []


# ------------------------------------------------ a supplied two-sub-query run, end to end (finding 5)


@pytest.mark.e2e
def test_a_supplied_decomposition_with_a_combine_group_runs_end_to_end(make):
    from nl2sql import SuppliedPlan
    from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
    from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse

    engine, server = make()
    decomposition = DecomposerResponse.model_validate(_join_decomposition(MUSIC_SPEND_SQ, OPEN_TICKETS_SQ))
    plan = SuppliedPlan(decomposition=decomposition, plans={
        "sq_music": PlanModel.model_validate(MUSIC_SPEND_PLAN),
        "sq_tickets": PlanModel.model_validate(OPEN_TICKETS_PLAN)})

    from nl2sql import UserContext

    result = engine.run_query("Which customers with an open ticket spent the most?",
                              user_context=UserContext(roles=["admin"]), plan=plan, answer=False)

    assert result.status == "success", result.errors
    assert {sq.plan_source for sq in result.sub_queries} == {"supplied"} and len(result.sub_queries) == 2
    [combined] = [m for m in result.result_manifest if m.node_id not in {"sq_music", "sq_tickets"}]
    assert combined.row_count == 5, "the join, sorted and cut to five by the post-combine step"
    assert server.calls == [], "not one model call"


# ------------------------------------------------ a supplied verified plan (findings 4 and 5)


def _verified(**params):
    from nl2sql import SuppliedPlan, VerifiedRef

    return SuppliedPlan(verified=VerifiedRef(id="top_customers_by_revenue", params=params or {"n": 5, "year": 2012}),
                        verified_datasource_id="chinook")


@pytest.mark.e2e
def test_a_supplied_verified_plan_runs_end_to_end_with_no_model_call(make):
    engine, server = make(curated=CURATED)

    result = engine.run_query("Top customers of 2012", user_context=_analyst(), plan=_verified(), answer=False)

    assert result.status == "success", result.errors
    assert result.source.type == "verified" and result.source.id == "top_customers_by_revenue"
    [sub] = result.sub_queries
    assert sub.plan_source == "verified" and len(sub.rows.rows) == 5
    assert server.calls == []


@pytest.mark.e2e
@pytest.mark.parametrize("case", ["execute_false", "no_curated_file", "other_datasource"])
def test_a_supplied_verified_plan_is_refused_at_the_call_when_it_cannot_run(make, case):
    engine, server = make(curated=None if case == "no_curated_file" else CURATED)
    kwargs = {"execute": False} if case == "execute_false" else {}
    if case == "other_datasource":
        kwargs["datasource_id"] = "support"

    with pytest.raises(ValueError):
        engine.run_query("Top customers of 2012", user_context=_analyst(), plan=_verified(), **kwargs)
    assert server.calls == []


@pytest.mark.e2e
@pytest.mark.parametrize("case, code", [
    ("unknown_query", "INVALID_STATE"),
    ("bad_param", "INVALID_STATE"),
    ("scope_miss", "SECURITY_VIOLATION"),
    ("policy_deny", "SECURITY_VIOLATION"),
])
def test_a_supplied_verified_plan_that_cannot_run_ends_with_an_error_and_no_model_call(make, case, code):
    from nl2sql import SuppliedPlan, UserContext, VerifiedRef

    engine, server = make(curated=CURATED)
    user = {"scope_miss": UserContext(roles=["nobody"]), "policy_deny": UserContext(roles=["viewer"])}.get(
        case, _analyst())
    plan = {"unknown_query": SuppliedPlan(verified=VerifiedRef(id="nope"), verified_datasource_id="chinook"),
            "bad_param": _verified(n=500, year=2012)}.get(case, _verified())

    result = engine.run_query("Top customers of 2012", user_context=user, plan=plan, answer=False)

    assert result.status == "error"
    assert code in [e["error_code"] for e in result.errors], result.errors
    assert all(sq.rows is None for sq in result.sub_queries)
    assert server.calls == [], "a supplied verified plan never falls back to a model run"


@pytest.mark.e2e
def test_a_stopped_run_with_answer_false_leaves_final_answer_none(make):
    from nl2sql import CancellationToken
    from nl2sql.limits import RunStop

    engine, server = make()
    token = CancellationToken()
    token.cancel(RunStop(limit="max_llm_calls", value=1, spent=1))

    stopped = engine.run_query(COUNT, user_context=_analyst(), plan=_supplied(), answer=False,
                               cancellation_token=token)
    with_answer = engine.run_query(COUNT, user_context=_analyst(), plan=_supplied(), cancellation_token=token)

    assert "RUN_LIMIT_REACHED" in [e["error_code"] for e in stopped.errors]
    assert stopped.final_answer is None
    assert with_answer.final_answer is not None, "the stop text is the answer when one is asked for"
    assert server.calls == []
