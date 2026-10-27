"""Asking again: ``run_query(choice=...)`` (wave_b.md, section 7; D49, L2).

The re-ask is stateless: the caller passes the request back with the option it picked. The engine checks the
request's consistency hash, the question as passed, the asker, the live schema version and knowledge digest and
the scope, then takes Wave A's pinned entry with the chosen option's plan (``plan_source="chosen"``).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_clarifier import ANA, GROSS, INVOICED, QUESTION, _sq
from test_plan_cache import store  # noqa: F401

from nl2sql.api.query_api import QueryAPI, QueryResult
from nl2sql.clarify.choice import choice_problem, pinned_from_choice
from nl2sql.clarify.models import ClarificationChoice, ClarificationOption, ClarificationRequest
from nl2sql.common.errors import ErrorCode
from nl2sql.explain.render import explain_plan
from nl2sql.pipeline.pinned import PinnedSource
from nl2sql.testing.runtime import fake_runtime


def _request(**changes) -> ClarificationRequest:
    options = [ClarificationOption(id=i, label=label, plan=plan.model_dump(mode="json"),
                                   explanation=explain_plan(plan, datasource_id="ds1", sub_query_id="sq1"))
               for i, label, plan in (("a", "Gross", GROSS), ("b", "Invoiced amount", INVOICED.plan))]
    fields = dict(question=QUESTION, asker_user_id="ana", asker_tenant_id="acme", phrase="sales",
                  datasource_id="ds1", schema_version="v1", knowledge_digest="d1",
                  sub_query=_sq().model_dump(mode="json"), options=options)
    return ClarificationRequest.build(**{**fields, **changes})


def _ctx(schema_version="v1", digest="d1", policies=None):
    return fake_runtime(
        policies=policies or {"allow_all": True}, ds_registry=SimpleNamespace(list_ids=lambda: ["ds1"]),
        vector_store=SimpleNamespace(schema_version=lambda ds: schema_version),
        knowledge=SimpleNamespace(digest=lambda ds: digest))


def _choice(option_id="b", **changes):
    return ClarificationChoice(request=_request(**changes), option_id=option_id)


def _problem(choice=None, *, question=QUESTION, user=ANA, ctx=None, approvals=()):
    return choice_problem(choice or _choice(), question=question, user=user, ctx=ctx or _ctx(),
                          approvals=approvals)


def test_a_consistent_choice_has_no_problem():
    assert _problem() is None


def test_choice_is_a_one_sub_query_standalone_run_of_the_chosen_plan():
    pinned = pinned_from_choice(_choice())

    assert pinned.source == PinnedSource.CHOSEN and pinned.chosen_label == "Invoiced amount"
    [sub_query] = pinned.decomposition.sub_queries
    assert sub_query.id == "sq1" and sub_query.datasource_id == "ds1"
    assert [g.operation for g in pinned.decomposition.combine_groups] == ["standalone"]
    assert pinned.plans == {"sq1": INVOICED.plan}


def test_question_is_compared_before_run_before():
    # The caller's question, as passed: the request's is compared with it, not with a hook's rewrite.
    assert _problem(question=QUESTION) is None
    assert _problem(question="Another question?").error_code == ErrorCode.CLARIFICATION_INVALID


@pytest.mark.parametrize("what", ["edited", "other_asker", "other_tenant", "no_such_option"])
def test_inconsistent_request_is_invalid(what):
    choice, user = _choice(), ANA
    if what == "edited":
        choice = ClarificationChoice(request=choice.request.model_copy(update={"phrase": "revenue"}),
                                     option_id="b")
    elif what == "other_asker":
        user = ANA.model_copy(update={"user_id": "bob"})
    elif what == "other_tenant":
        user = ANA.model_copy(update={"tenant_id": "other"})
    else:
        choice = _choice("z")

    assert _problem(choice, user=user).error_code == ErrorCode.CLARIFICATION_INVALID


@pytest.mark.parametrize("ctx", [_ctx(schema_version="v2"), _ctx(digest="d2")])
def test_changed_schema_or_glossary_is_stale(ctx):
    problem = _problem(ctx=ctx)

    assert problem.error_code == ErrorCode.CLARIFICATION_STALE
    assert "Ask again" in problem.message


def test_denied_datasource_in_a_choice_is_refused():
    ctx = _ctx(policies={"roles": {"user": {"description": "d", "allow": ["other.main.t"]}}})

    assert _problem(ctx=ctx).error_code == ErrorCode.CLARIFICATION_STALE


def test_choice_and_approvals_together_are_refused():
    problem = _problem(approvals=[object()])

    assert problem.error_code == ErrorCode.CLARIFICATION_INVALID and "approval" in problem.message


# ---------------------------------------------------------------- run_query


class _Graph:
    def __init__(self, monkeypatch):
        self.calls = []
        monkeypatch.setattr("nl2sql.api.query_api.run_with_graph", self._run)

    def _run(self, ctx, question, **kwargs):
        self.calls.append(kwargs)
        return {"trace_id": "t", "user_query": question, "user_context": ANA}


def _result(api, how, **kwargs):
    if how == "run_query":
        return api.run_query(QUESTION, user_context=ANA, **kwargs)
    events = list(api.stream_query(QUESTION, user_context=ANA, **kwargs))
    assert events[-1].name == "run.finished"
    return QueryResult.model_validate(events[-1].data["result"])


@pytest.mark.parametrize("how", ["run_query", "stream_query"])
def test_choice_hands_the_chosen_plan_to_the_pinned_run(monkeypatch, how):
    graph = _Graph(monkeypatch)

    _result(QueryAPI(_ctx()), how, choice=_choice())

    [kwargs] = graph.calls
    assert kwargs["pinned"].source == PinnedSource.CHOSEN and kwargs["pinned"].plans == {"sq1": INVOICED.plan}
    assert not kwargs.get("approvals")


@pytest.mark.parametrize("how", ["run_query", "stream_query"])
def test_a_refused_choice_ends_the_run_without_running_it(monkeypatch, how):
    graph = _Graph(monkeypatch)

    result = _result(QueryAPI(_ctx(schema_version="v2")), how, choice=_choice())

    assert graph.calls == []
    assert result.status == "error" and result.clarification is None
    assert [e["error_code"] for e in result.errors] == ["CLARIFICATION_STALE"]


def test_choice_and_approvals_are_refused_with_the_code(monkeypatch):
    graph = _Graph(monkeypatch)

    result = QueryAPI(_ctx()).run_query(QUESTION, user_context=ANA, choice=_choice(), approvals=[object()])

    assert graph.calls == [] and [e["error_code"] for e in result.errors] == ["CLARIFICATION_INVALID"]


def test_choice_with_a_plan_or_a_pin_is_refused_at_the_call(monkeypatch):
    _Graph(monkeypatch)

    with pytest.raises(ValueError, match="choice"):
        QueryAPI(_ctx()).run_query(QUESTION, user_context=ANA, choice=_choice(), pinned=pinned_from_choice(_choice()))


def test_the_facade_takes_choice():
    import inspect

    from nl2sql import NL2SQL

    assert "choice" in inspect.signature(NL2SQL.run_query).parameters
    assert "choice" in inspect.signature(NL2SQL.stream_query).parameters


def test_the_explanation_says_what_you_chose():
    from nl2sql.explain.render import explain_state

    pinned = pinned_from_choice(_choice())
    output = {"status": "success", "sub_query": _sq(), "plan": INVOICED.plan, "plan_source": "chosen"}
    state = {"subgraph_outputs": {"sq1": output}, "decomposer_response": pinned.decomposition, "pinned": pinned}

    explanation = explain_state(state, [], None)

    assert "You chose: Invoiced amount" in explanation.steps[0].assumptions
    assert explanation.steps[0].source == "chosen"


def test_chosen_plan_needing_cost_approval_round_trips():
    # A hook answers needs_approval on the chosen plan: the request pins the chosen run's one-sub-query
    # decomposition and plan, and the approver's re-run (approvals alone) pins them as approved.
    from test_approval import ADMIN, _runner, NEEDS

    from nl2sql.hooks.approval import Approval, ApprovalRequest

    chosen = pinned_from_choice(_choice())
    [sub_query] = chosen.decomposition.sub_queries
    request = ApprovalRequest.build(question=QUESTION, asker=ANA, hook="review", datasource_id="ds1",
                                    sql="SELECT 1", reason="Reads a lot", decomposition=chosen.decomposition,
                                    sub_query=sub_query, plan=chosen.plans["sq1"])

    from nl2sql.pipeline.pinned import pinned_from_approvals

    rerun = pinned_from_approvals([Approval(request=request, by=ADMIN)], question=QUESTION, user=ANA,
                                  hooks=_runner(NEEDS, roles=("admin",)))

    assert rerun.source == PinnedSource.APPROVED and rerun.plans == chosen.plans
    assert rerun.decomposition == chosen.decomposition


# ---------------------------------------------------------------- the pinned run of a chosen plan


def test_choice_runs_the_chosen_plan_through_the_validator(monkeypatch):
    from test_hook_points import ORDERS, SALARIES
    from test_supplied_plan import _sql_agent

    broken = ORDERS.model_copy(update={"select_items": [
        ORDERS.select_items[0].model_copy(update={"expr": ORDERS.select_items[0].expr.model_copy(
            update={"column_name": "no_such_column"})})]})

    generated, refined, result = _sql_agent(monkeypatch, ORDERS, source=PinnedSource.CHOSEN)
    assert [s.ast_planner_response.plan_source for s in generated] == ["chosen"]

    # A plan edited in the caller's hands buys nothing the validator would not allow.
    for plan, code in ((SALARIES, ErrorCode.SECURITY_VIOLATION), (broken, None)):
        generated, refined, result = _sql_agent(monkeypatch, plan, source=PinnedSource.CHOSEN)
        assert generated == [] and refined == []
        assert result["errors"] and (code is None or any(e.error_code == code for e in result["errors"]))


def test_choice_skips_resolver_decomposer_and_datasources_after():
    from nl2sql.hooks import Allow, HookBinding, HookRunner
    from nl2sql.pipeline.pinned import PinnedEntryNode
    from nl2sql.pipeline.state import GraphState

    seen = []
    hooks = HookRunner([HookBinding(name="spy", hook=lambda ctx: seen.append(ctx) or Allow(),
                                    points=frozenset({"datasources.after"}))])
    entry = PinnedEntryNode(fake_runtime(policies={"allow_all": True}, hooks=hooks,
                                         ds_registry=SimpleNamespace(list_ids=lambda: ["ds1"])))
    pinned = pinned_from_choice(_choice())

    out = entry(GraphState(user_query=QUESTION, user_context=ANA, pinned=pinned))

    assert out["decomposer_response"] == pinned.decomposition and not out.get("errors")
    assert seen == []


def test_chosen_plan_is_not_cached(store):
    from test_plan_cache import _artifact, _executed, _run_wrapped, _sq as cache_sq

    from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse
    from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
    from nl2sql.pipeline.plan_cache import PlanCache

    out = _run_wrapped(store, {
        "ast_planner_response": ASTPlannerResponse(plan=INVOICED.plan, plan_source="chosen"),
        "generator_response": GeneratorResponse(sql_draft="SELECT 1"), "executor_response": _executed(_artifact())})

    assert out["subgraph_outputs"]["sql_agent:sq1:t1"].plan_source == "chosen"
    assert PlanCache(store).get(cache_sq()) is None
