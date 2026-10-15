"""Approval and the pinned-plan entry end to end on the demo databases, with no key.

A hook at ``execute.before`` asks for approval to read customers. The asker's
run ends ``needs_approval`` with a request; the approver re-runs the question
with an ``Approval``, which pins the decomposition and the plan (no resolver,
decomposer or planner call), regenerates the SQL and checks its hash. A forged
approval asks again. ``run_query(plan=SuppliedPlan(...))`` runs a plan the
caller holds through the same entry, under the caller's scope.
"""
from __future__ import annotations

import pytest

from nl2sql.hooks import Allow, NeedsApproval

from .recordings_chinook import COUNT_CUSTOMERS_DECOMPOSER, COUNT_CUSTOMERS_PLAN, RULES_COUNT_CUSTOMERS
from .test_result_paging_fake_llm import _env

COUNT = "How many customers are there?"


class Review:
    """Customer data needs a person's yes."""

    name = "review"
    points = frozenset({"execute.before"})

    def __call__(self, ctx):
        return NeedsApproval(reason="Reads customer data") if "Customer" in (ctx.sql or "") else Allow()


@pytest.fixture
def engine(demo_project, fake_llm, monkeypatch):
    from nl2sql import NL2SQL
    from nl2sql.config import load_config

    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
    path = _env(monkeypatch, demo_project, env)
    config = load_config(path)
    built = NL2SQL(config.model_copy(update={"hooks": [Review()]}))
    yield built, server
    built.close()


def _asker():
    from nl2sql import UserContext

    return UserContext(user_id="ana", tenant_id="acme", roles=["analyst"])


def _calls(server, since=0):
    return [c["name"] for c in server.calls[since:]]


@pytest.mark.e2e
def test_an_approved_rerun_runs_the_pinned_plan_and_the_same_sql(engine):
    from nl2sql import Approval, UserContext

    engine, server = engine
    first = engine.run_query(COUNT, user_context=_asker())

    assert first.status == "needs_approval", first.errors
    [request] = first.approvals_needed
    assert request.question == COUNT and request.asker_user_id == "ana" and request.hook == "review"
    assert "Customer" in request.sql and request.plan is not None and request.decomposition is not None
    assert request.approver_roles == ["admin"]
    assert all(sq.rows is None for sq in first.sub_queries)

    before = len(server.calls)
    second = engine.run_query(COUNT, user_context=_asker(),
                              approvals=[Approval(request=request, by=UserContext(user_id="root", roles=["admin"]))])

    assert second.status == "success", second.errors
    [sub] = second.sub_queries
    assert sub.plan_source == "approved" and sub.sql == request.sql
    assert sub.rows.rows == [[59]]
    # No routing, decomposing or planning: only the answer was written.
    assert _calls(server, before) == ["AggregatedResponse"]


@pytest.mark.e2e
def test_a_forged_approval_asks_again(engine):
    from nl2sql import Approval, UserContext

    engine, server = engine
    [request] = engine.run_query(COUNT, user_context=_asker()).approvals_needed

    viewer = UserContext(user_id="vic", roles=["viewer"])
    for approval, asker in [
        (Approval(request=request, by=viewer), _asker()),  # an approver without the role
        (Approval(request=request.model_copy(update={"approver_roles": ["viewer"]}), by=viewer), _asker()),
        (Approval(request=request, by=UserContext(roles=["admin"])),
         UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])),  # another asker
    ]:
        result = engine.run_query(COUNT, user_context=asker, approvals=[approval])
        assert result.status == "needs_approval", result.errors
        assert all(sq.rows is None for sq in result.sub_queries)


@pytest.mark.e2e
def test_an_approval_works_on_the_stream(engine):
    from nl2sql import Approval, UserContext

    engine, _server = engine
    [request] = engine.run_query(COUNT, user_context=_asker()).approvals_needed

    events = list(engine.stream_query(COUNT, user_context=_asker(),
                                      approvals=[Approval(request=request, by=UserContext(roles=["admin"]))]))

    result = events[-1].data["result"]
    assert result["status"] == "success" and result["sub_queries"][0]["plan_source"] == "approved"


def _supplied(datasource_id="chinook"):
    from nl2sql import SuppliedPlan
    from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
    from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery

    sub_query = SubQuery.model_validate({**COUNT_CUSTOMERS_DECOMPOSER["sub_queries"][0],
                                         "datasource_id": datasource_id})
    return SuppliedPlan(datasource_id=datasource_id, sub_query=sub_query,
                        plan=PlanModel.model_validate(COUNT_CUSTOMERS_PLAN))


@pytest.mark.e2e
def test_a_supplied_plan_runs_with_no_planning(demo_project, fake_llm, monkeypatch):
    from nl2sql import NL2SQL, UserContext

    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
    _env(monkeypatch, demo_project, env)
    engine = NL2SQL.from_config()
    try:
        result = engine.run_query(COUNT, user_context=UserContext(roles=["analyst"]), plan=_supplied())
    finally:
        engine.close()

    assert result.status == "success", result.errors
    [sub] = result.sub_queries
    assert sub.plan_source == "supplied" and sub.rows.rows == [[59]]
    assert _calls(server) == ["AggregatedResponse"]


@pytest.mark.e2e
@pytest.mark.parametrize("datasource_id", ["chinook", "support"])
def test_a_supplied_plan_outside_the_callers_scope_is_refused(demo_project, fake_llm, monkeypatch, datasource_id):
    from nl2sql import NL2SQL, UserContext

    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
    _env(monkeypatch, demo_project, env)
    engine = NL2SQL.from_config()
    try:
        # The viewer reads neither chinook's customers nor anything in support.
        result = engine.run_query(COUNT, user_context=UserContext(roles=["viewer"]), plan=_supplied(datasource_id))
    finally:
        engine.close()

    assert result.status == "error"
    assert "SECURITY_VIOLATION" in [e["error_code"] for e in result.errors]
    assert all(sq.rows is None for sq in result.sub_queries)
    assert _calls(server) == []
