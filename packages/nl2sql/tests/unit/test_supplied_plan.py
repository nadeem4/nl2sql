"""The pinned-plan entry: ``run_query(plan=SuppliedPlan(...))`` and the approval re-run (wave_a.md, section 7).

A pinned plan skips the curated matcher, the resolver, the decomposer and the
planner's model call. Its datasource is scope-checked for the caller, and the
plan still passes ``plan.after``, the validator and the generator; it is never
cached, and a validator failure ends the sub-query with the validator's error
instead of asking the refiner for another plan.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from test_hook_points import ORDERS, SALARIES, _hooks
from test_plan_cache import _plan, _sq, store  # noqa: F401
from test_approval import ANA, DBA, NEEDS, _approval, _request, _runner

from nl2sql.common.errors import ErrorCode
from nl2sql.hooks import Allow, Deny, Modify
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.ast_planner.schemas import PlannerAnswer
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.pinned import PinnedEntryNode, PinnedRun, PinnedSource, SuppliedPlan, pinned_from_approvals
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime


def test_supplied_plan_is_public_and_its_datasource_is_its_sub_querys():
    import nl2sql

    assert nl2sql.SuppliedPlan is SuppliedPlan
    with pytest.raises(ValidationError):
        SuppliedPlan(datasource_id="hr", sub_query=_sq(ds="chinook"), plan=_plan())


def test_the_three_sources_of_a_pinned_plan():
    assert {s.value for s in PinnedSource} == {"supplied", "approved", "chosen"}
    pinned = PinnedRun.supplied(SuppliedPlan(datasource_id="chinook", sub_query=_sq(), plan=_plan()))
    assert pinned.source == PinnedSource.SUPPLIED and pinned.plans == {"sq1": _plan()}
    [group] = pinned.decomposition.combine_groups
    assert group.operation == "standalone"


# ---------------------------------------------------------------- the planner


def _planner(store, hooks):
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = fake_runtime(llm_registry=MagicMock(), state=store, hooks=hooks)
    ctx.llm_registry.get_llm.return_value = llm
    ctx.llm_registry.effective.return_value = None
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = _plan("from the model")
    return node


@pytest.mark.parametrize("source", list(PinnedSource))
def test_approved_rerun_uses_the_pinned_plan_even_when_the_model_would_replan(store, source):
    hooks = _hooks(lambda ctx: Allow())
    node = _planner(store, hooks)

    out = node(SubgraphExecutionState(trace_id="t", sub_query=_sq(), pinned_plan=_plan("pinned"),
                                      pinned_source=source))

    node.chain.invoke.assert_not_called()
    response = out["ast_planner_response"]
    assert response.plan.reasoning == "pinned" and response.plan_source == source.value
    # It still passed plan.after.
    assert [ctx.plan.reasoning for _, ctx in hooks.seen] == ["pinned"]


def test_a_pinned_plan_cannot_skip_plan_after(store):
    node = _planner(store, _hooks(lambda ctx: Deny(reason="Not this plan")))

    out = node(SubgraphExecutionState(trace_id="t", sub_query=_sq(), pinned_plan=_plan("pinned"),
                                      pinned_source=PinnedSource.SUPPLIED))

    assert out["ast_planner_response"].plan is None
    assert out["errors"][0].error_code == ErrorCode.HOOK_DENIED


# ---------------------------------------------------------------- the SQL agent


def _sql_agent(monkeypatch, pinned, hook=lambda ctx: Allow(), role="user", source=PinnedSource.SUPPLIED):
    """The real planner and validator, retries on, for a role that may read orders only."""
    from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
    from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph

    generated, refined = [], []
    tables = [Table(name="orders", columns=[Column(name="id", type="int")])]
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.SchemaRetrieverNode",
                        lambda _ctx: (lambda s: {"relevant_tables": tables}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.GeneratorNode",
                        lambda _ctx: (lambda s: generated.append(s) or {}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.RefinerNode",
                        lambda _ctx: (lambda s: refined.append(s) or {}))
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = fake_runtime(llm_registry=MagicMock(), ds_registry=SimpleNamespace(), hooks=_hooks(hook),
                       policies={"roles": {"user": {"description": "d", "allow": ["ds1.main.orders"]}}},
                       settings={"pipeline": {"retries": {"max": 2, "base_delay_s": 0, "jitter_s": 0}}})
    ctx.llm_registry.get_llm.return_value = llm
    ctx.llm_registry.effective.return_value = None
    monkeypatch.setattr("nl2sql.pipeline.nodes.ast_planner.node.structured",
                        lambda _llm, _model: RunnableLambda(lambda _inputs: PlannerAnswer(plan=ORDERS)))

    result = build_sql_agent_graph(ctx, execute=False).invoke(SubgraphExecutionState(
        trace_id="t", user_query="q", sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        user_context=UserContext(roles=[role]), pinned_plan=pinned, pinned_source=source))
    return generated, refined, result


def test_a_supplied_plan_outside_the_callers_tables_is_refused_by_the_validator(monkeypatch):
    generated, refined, result = _sql_agent(monkeypatch, SALARIES)

    assert generated == []
    assert any(e.error_code == ErrorCode.SECURITY_VIOLATION for e in result["errors"])


def test_a_pinned_plan_that_fails_validation_never_asks_the_refiner(monkeypatch):
    broken = ORDERS.model_copy(update={"select_items": [
        ORDERS.select_items[0].model_copy(update={"expr": ORDERS.select_items[0].expr.model_copy(
            update={"column_name": "no_such_column"})})]})

    generated, refined, result = _sql_agent(monkeypatch, broken)

    assert refined == [] and generated == []
    assert result["retry_count"] == 0
    assert result["errors"] and all(e.node == "logical_validator" for e in result["errors"])


def test_a_plan_after_change_to_a_pinned_plan_is_validated(monkeypatch):
    generated, refined, result = _sql_agent(monkeypatch, ORDERS, hook=lambda ctx: Modify(plan=SALARIES))

    assert generated == [] and refined == []
    assert any(e.error_code == ErrorCode.SECURITY_VIOLATION for e in result["errors"])


def test_a_valid_supplied_plan_reaches_the_generator(monkeypatch):
    generated, refined, result = _sql_agent(monkeypatch, ORDERS)

    [state] = generated
    assert state.ast_planner_response.plan_source == "supplied"


# ---------------------------------------------------------------- the entry


def _entry(allow):
    ctx = fake_runtime(policies={"roles": {"user": {"description": "d", "allow": allow}}},
                       ds_registry=SimpleNamespace(list_ids=lambda: ["chinook", "hr"]))
    return PinnedEntryNode(ctx)


def _pinned_state(ds="chinook", user=UserContext(roles=["user"])):
    plan = SuppliedPlan(datasource_id=ds, sub_query=_sq(ds=ds), plan=_plan())
    return GraphState(user_query="q", user_context=user, pinned=PinnedRun.supplied(plan))


def test_approved_rerun_skips_the_resolver_and_decomposer():
    state = _pinned_state()
    out = _entry(["chinook"])(state)

    assert [sq.id for sq in out["decomposer_response"].sub_queries] == ["sq1"]
    assert out["execution_dag"].layers[0] == ["sq1"]
    assert not out.get("errors")


def test_a_supplied_plan_for_a_datasource_outside_the_callers_scope_is_refused():
    from nl2sql.pipeline.routes import pinned_route

    out = _entry(["chinook"])(_pinned_state(ds="hr"))

    assert out["errors"][0].error_code == ErrorCode.SECURITY_VIOLATION
    assert "decomposer_response" not in out
    assert pinned_route(GraphState(user_query="q", **out)) == "end"


def test_only_a_pinned_run_enters_at_the_pinned_entry(monkeypatch):
    from nl2sql.pipeline import graph as control

    for name in ("CuratedMatcherNode", "DatasourceResolverNode", "DecomposerNode", "EngineAggregatorNode",
                 "AnswerSynthesizerNode"):
        monkeypatch.setattr(control, name, lambda ctx: (lambda state: {}))
    monkeypatch.setattr(control, "build_sql_agent_graph", lambda ctx, execute=True: (lambda state: {}))

    def first(pinned):
        graph = control.build_graph(fake_runtime(), pinned=pinned).get_graph()
        return [e.target for e in graph.edges if e.source == "__start__"]

    assert first(True) == ["pinned_entry"]
    assert first(False) == ["datasource_resolver"]


# ---------------------------------------------------------------- from approvals


def test_a_valid_approval_pins_its_decomposition_and_plan():
    pinned = pinned_from_approvals([_approval()], question="How many customers?", user=ANA, hooks=_runner(NEEDS))

    assert pinned.source == PinnedSource.APPROVED
    assert pinned.plans == {"sq1": _plan()} and pinned.decomposition.sub_queries[0].id == "sq1"


@pytest.mark.parametrize("approval, question, user", [
    (_approval(), "How many customers?", UserContext(user_id="bob", tenant_id="acme")),  # another asker
    (_approval(), "Something else?", ANA),  # another question
    (_approval(_request().model_copy(update={"plan": _plan("tampered")})), "How many customers?", ANA),  # edited
])
def test_a_forged_approval_pins_nothing(approval, question, user):
    assert pinned_from_approvals([approval], question=question, user=user, hooks=_runner(NEEDS)) is None


def test_chosen_plan_with_approvals_is_refused():
    from nl2sql.pipeline.runtime import run_with_graph

    chosen = PinnedRun.supplied(SuppliedPlan(datasource_id="chinook", sub_query=_sq(), plan=_plan()))
    chosen = chosen.model_copy(update={"source": PinnedSource.CHOSEN})

    with pytest.raises(ValueError, match="approvals"):
        run_with_graph(fake_runtime(), "q", pinned=chosen, approvals=[_approval()])


# ---------------------------------------------------------------- the facade


@pytest.mark.parametrize("how", ["run_query", "stream_query"])
def test_the_facade_passes_the_plan_and_the_approvals_to_the_run(monkeypatch, how):
    from nl2sql.api.query_api import QueryAPI

    seen = {}

    def fake_run(ctx, question, **kwargs):
        seen.update(kwargs)
        return {"trace_id": "t"}

    monkeypatch.setattr("nl2sql.api.query_api.run_with_graph", fake_run)
    api = QueryAPI(fake_runtime())
    supplied = SuppliedPlan(datasource_id="chinook", sub_query=_sq(), plan=_plan())
    approvals = [_approval(by=DBA)]

    if how == "run_query":
        api.run_query("q", plan=supplied, approvals=approvals)
    else:
        list(api.stream_query("q", plan=supplied, approvals=approvals))

    assert seen["pinned"].source == PinnedSource.SUPPLIED and seen["approvals"] == approvals


# ---------------------------------------------------------------- the review's attacks (PR #319)


def test_an_approver_without_the_role_pins_nothing():
    viewer = UserContext(user_id="vic", roles=["viewer"])

    assert pinned_from_approvals([_approval(by=viewer)], question="How many customers?", user=ANA,
                                 hooks=_runner(NEEDS)) is None


def test_an_approval_for_a_hook_that_is_not_configured_pins_nothing():
    from nl2sql.hooks import HookRunner

    # Built by the asker, with no approval hook configured: nobody approved anything.
    self_built = _approval(by=UserContext(user_id="nobody", roles=["admin"]))

    assert pinned_from_approvals([self_built], question="How many customers?", user=ANA,
                                 hooks=HookRunner([], admin_roles=("admin",))) is None


def test_a_datasource_id_that_disagrees_with_the_supplied_plan_is_refused_at_the_call(monkeypatch):
    from nl2sql.api.query_api import QueryAPI

    api = QueryAPI(fake_runtime())
    started = []
    api._ctx.bus.subscribe(started.append)
    supplied = SuppliedPlan(datasource_id="chinook", sub_query=_sq(), plan=_plan())

    with pytest.raises(ValueError, match="datasource"):
        api.run_query("q", datasource_id="support", plan=supplied)
    with pytest.raises(ValueError, match="datasource"):
        api.stream_query("q", datasource_id="support", plan=supplied)
    assert started == []  # no scope was opened


@pytest.mark.parametrize("how", ["run_query", "stream_query"])
def test_a_chosen_plan_with_approvals_is_refused_before_the_scope_opens(how):
    from nl2sql.api.query_api import QueryAPI

    api = QueryAPI(fake_runtime())
    started = []
    api._ctx.bus.subscribe(started.append)
    chosen = PinnedRun.supplied(SuppliedPlan(datasource_id="chinook", sub_query=_sq(), plan=_plan()))
    chosen = chosen.model_copy(update={"source": PinnedSource.CHOSEN})

    with pytest.raises(ValueError, match="approvals"):
        getattr(api, how)("q", pinned=chosen, approvals=[_approval()])
    assert started == []


def test_the_rest_schema_lists_the_plan_sources():
    from nl2sql.api.query_api import SubQueryResult

    schema = SubQueryResult.model_json_schema()["properties"]["plan_source"]
    assert set(schema["enum"]) == {"llm", "cache", "verified", "supplied", "approved", "chosen"}
