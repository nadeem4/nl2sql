"""Approval (docs/architecture/wave_a.md, section 7; D10, D24 amended).

A hook at ``execute.before`` answers ``NeedsApproval``: the run ends
``needs_approval`` with an ``ApprovalRequest`` per pending sub-query. An
approver re-runs the question with an ``Approval``; the engine, not the hook,
checks it: the request's recomputed id, the same asker and question, the
hook's configured approver roles (never the request's), and the hash of the
SQL generated again. An accepted approval counts as ``Allow`` and the next
hook still runs.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_curated_matcher import CHOOSE_TOP, COUNT, QUESTION, TOP, _ctx, _server, db  # noqa: F401
from test_plan_cache import _artifact, _executed, _plan, _sq, store  # noqa: F401

from nl2sql.common.errors import ErrorCode
from nl2sql.events import EventBus, RunScope
from nl2sql.hooks import Allow, Deny, HookBinding, HookRunner, NeedsApproval
from nl2sql.hooks.approval import Approval, ApprovalRequest, VerifiedRef, sql_sha256
from nl2sql.pipeline.graph_utils import wrap_subgraph
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.plan_cache import PlanCache
from nl2sql.pipeline.state import GraphState
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime

ANA = UserContext(user_id="ana", tenant_id="acme", roles=["analyst"])
DBA = UserContext(user_id="dba", tenant_id="acme", roles=["dba"])
ADMIN = UserContext(user_id="root", tenant_id="acme", roles=["admin"])
SQL = "SELECT COUNT(t1.CustomerId) AS n FROM Customer AS t1"
DECOMPOSITION = DecomposerResponse(
    sub_queries=[_sq()],
    combine_groups=[CombineGroup(group_id="g1", operation="standalone", inputs=[CombineInput(subquery_id="sq1")])])


def _request(**changes):
    fields = dict(question="How many customers?", asker=ANA, hook="review", datasource_id="chinook", sql=SQL,
                  reason="Reads customers", decomposition=DECOMPOSITION, sub_query=_sq(), plan=_plan())
    fields.update(changes)
    return ApprovalRequest.build(**fields)


def _approval(request=None, by=DBA):
    return Approval(request=request or _request(), by=by)


def _runner(*decide, roles=("dba",), admin_roles=("admin",)):
    names = ["review", "audit", "third"]
    return HookRunner([HookBinding(name=names[i], hook=fn, points=frozenset({"execute.before"}),
                                   approver_roles=roles if i == 0 else None)
                       for i, fn in enumerate(decide)], admin_roles=admin_roles)


def _call(runner, approvals=(), *, question="How many customers?", user=ANA, sql=SQL, ds="chinook", params=None):
    return runner.call("execute.before", trace_id="t", question=question, user=user, datasource_id=ds,
                       sub_query=_sq(), sql=sql, params=params, approvals=list(approvals))


NEEDS = lambda ctx: NeedsApproval(reason="Reads customers")  # noqa: E731


# ---------------------------------------------------------------- the request


def test_the_request_id_hashes_everything_but_the_reason_and_the_roles():
    request = _request()

    assert request.sql_sha256 == sql_sha256(SQL)
    assert request.id == request.expected_id()
    assert _request(reason="Something else", approver_roles=["x"]).id == request.id
    assert _request(question="Another question?").id != request.id
    assert _request(asker=UserContext(user_id="bob", tenant_id="acme")).id != request.id
    assert _request(sql=SQL + " WHERE 1 = 1").id != request.id
    # Stable through JSON, so an app (or Wave D's store) can hold it and send it back.
    again = ApprovalRequest.model_validate_json(request.model_dump_json())
    assert again.expected_id() == request.id


def test_an_edited_request_no_longer_matches_its_id():
    edited = _request().model_copy(update={"plan": _plan("a different plan")})

    assert edited.expected_id() != edited.id


# ---------------------------------------------------------------- the runner's check


def test_matching_approval_allows():
    verdict = _call(_runner(NEEDS), [_approval()])

    assert verdict.allowed


def test_needs_approval_without_an_approval_stops_the_point_with_the_reason():
    verdict = _call(_runner(NEEDS))

    assert isinstance(verdict.decision, NeedsApproval) and verdict.hook == "review"
    error = verdict.error("executor")
    assert error.error_code == ErrorCode.NEEDS_APPROVAL
    assert error.details == {"hook": "review", "point": "execute.before", "reason": "Reads customers"}


def test_next_hook_runs_after_an_approval():
    seen = []
    runner = _runner(NEEDS, lambda ctx: seen.append(ctx.point) or Deny(reason="Second opinion says no"))

    verdict = _call(runner, [_approval()])

    assert seen == ["execute.before"]
    assert isinstance(verdict.decision, Deny) and verdict.hook == "audit"


def test_the_hook_sees_the_approvals():
    seen = []
    runner = _runner(lambda ctx: seen.append([a.by.user_id for a in ctx.approvals]) or NeedsApproval(reason="r"))

    _call(runner, [_approval()])

    assert seen == [["dba"]]


def test_approval_for_other_sql_asks_again():
    verdict = _call(_runner(NEEDS), [_approval()], sql=SQL + " LIMIT 5")

    assert isinstance(verdict.decision, NeedsApproval)


def test_approval_for_another_asker_is_not_accepted():
    bob = UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])
    other_tenant = UserContext(user_id="ana", tenant_id="globex", roles=["analyst"])

    assert not _call(_runner(NEEDS), [_approval()], user=bob).allowed
    assert not _call(_runner(NEEDS), [_approval()], user=other_tenant).allowed


def test_approval_for_another_question_is_not_accepted():
    assert not _call(_runner(NEEDS), [_approval()], question="How many invoices?").allowed


def test_edited_request_is_not_accepted():
    # Pointed at other SQL with a hash to match, but the id was not recomputed.
    tampered = _request().model_copy(update={"sql": "SELECT * FROM Employee",
                                             "sql_sha256": sql_sha256("SELECT * FROM Employee")})

    assert not _call(_runner(NEEDS), [_approval(tampered)], sql="SELECT * FROM Employee").allowed


def test_approver_without_role_is_not_accepted():
    # The request lists the analyst role; the engine checks the hook's configured roles (dba) only.
    listed = _request(approver_roles=["analyst"])

    assert not _call(_runner(NEEDS), [_approval(listed, by=ANA)]).allowed
    assert not _call(_runner(NEEDS), [_approval(by=ADMIN)]).allowed  # admin is not this hook's approver
    assert _call(_runner(NEEDS), [_approval(by=DBA)]).allowed


def test_without_configured_roles_the_admin_roles_approve():
    runner = _runner(NEEDS, roles=None, admin_roles=("admin",))

    assert _call(runner, [_approval(by=ADMIN)]).allowed
    assert not _call(runner, [_approval(by=DBA)]).allowed


def test_approval_for_another_hook_or_datasource_is_not_accepted():
    assert not _call(_runner(NEEDS), [_approval(_request(hook="other"))]).allowed
    assert not _call(_runner(NEEDS), [_approval(_request(datasource_id="hr"))]).allowed


def test_approval_for_other_verified_values_asks_again():
    verified = _request(decomposition=None, sub_query=None, plan=None,
                        verified=VerifiedRef(id="top", params={"n": 2}))

    assert _call(_runner(NEEDS), [_approval(verified)], params={"n": 2}).allowed
    assert not _call(_runner(NEEDS), [_approval(verified)], params={"n": 2000}).allowed


def test_needs_approval_outside_execute_before_denies():
    runner = HookRunner([HookBinding(name="review", hook=NEEDS, points=frozenset({"plan.after"}))])

    verdict = runner.call("plan.after", trace_id="t", question="q", user=ANA, plan=_plan(),
                          approvals=[_approval()])

    assert verdict.failed and verdict.error("astplanner").error_code == ErrorCode.HOOK_FAILED


def test_approval_is_audited():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append, names=["hook.decision"])
    scope = RunScope(bus, question="How many customers?", user_context=ANA)
    scope.start()
    with scope.entered():
        _call(_runner(NEEDS), [_approval()])
        _call(_runner(NEEDS))

    decisions = [(e.data["decision"], e.data.get("approver")) for e in seen]
    assert decisions == [("approved", "dba"), ("needs_approval", None)]


# ---------------------------------------------------------------- the request on the result


def _wrapped(returned, *, hooks=None, state=None):
    ctx = fake_runtime(state=state, hooks=hooks or _runner(NEEDS))
    return wrap_subgraph(SimpleNamespace(invoke=lambda s, config=None: {**s, **returned}), "sql_agent", ctx)({
        "trace_id": "t1", "subgraph_id": "sql_agent:sq1:t1", "user_query": "How many customers?",
        "user_context": ANA, "decomposer_response": DECOMPOSITION})


def test_needs_approval_ends_the_run_with_a_request():
    error = _call(_runner(NEEDS)).error("executor")
    hooked = _plan("changed by plan.after")

    out = _wrapped({"ast_planner_response": ASTPlannerResponse(plan=hooked, planner_plan=_plan()),
                    "generator_response": GeneratorResponse(sql_draft=SQL),
                    "executor_response": None, "errors": [error]})

    [request] = out["approvals_needed"]
    assert request.id == request.expected_id()
    assert (request.question, request.asker_user_id, request.asker_tenant_id) == ("How many customers?", "ana", "acme")
    assert (request.hook, request.point, request.datasource_id) == ("review", "execute.before", "chinook")
    assert request.decomposition == DECOMPOSITION and request.sub_query.id == "sq1"
    # The plan as plan.after got it: the re-run passes it through plan.after again.
    assert request.plan == _plan()
    assert request.sql == SQL and request.sql_sha256 == sql_sha256(SQL)
    assert request.reason == "Reads customers" and request.approver_roles == ["dba"]
    assert out["errors"][0].error_code == ErrorCode.NEEDS_APPROVAL


def test_needs_approval_result_status(monkeypatch):
    from nl2sql.api.query_api import result_from_state

    error = _call(_runner(NEEDS)).error("executor")
    out = _wrapped({"ast_planner_response": ASTPlannerResponse(plan=_plan()),
                    "generator_response": GeneratorResponse(sql_draft=SQL),
                    "executor_response": None, "errors": [error]})
    state = GraphState(user_query="q", **out).model_dump()

    result = result_from_state(state)

    assert result.status == "needs_approval"
    assert [r.reason for r in result.approvals_needed] == ["Reads customers"]


@pytest.mark.parametrize("source", ["approved", "supplied", "chosen"])
def test_approved_plan_is_never_cached(store, source):
    _wrapped({"ast_planner_response": ASTPlannerResponse(plan=_plan(), plan_source=source),
              "generator_response": GeneratorResponse(sql_draft=SQL),
              "executor_response": _executed(_artifact())}, state=store)

    assert PlanCache(store).get(_sq()) is None


# ---------------------------------------------------------------- the verified path


def _verified(db, hooks, pinned=None, approvals=()):
    from nl2sql.pipeline.nodes.curated_matcher import CuratedMatcherNode

    server = _server(CHOOSE_TOP)
    try:
        ctx = _ctx(db, server, [TOP, COUNT])
        ctx.hooks = hooks
        out = CuratedMatcherNode(ctx)(GraphState(user_query=QUESTION, user_context=ANA.model_copy(
            update={"roles": ["analyst"]}), pinned=pinned, approvals=list(approvals)))
        return out, server
    finally:
        server.stop()


def test_verified_query_can_need_approval(db):
    out, _ = _verified(db, _runner(NEEDS))

    [request] = out["approvals_needed"]
    assert request.verified == VerifiedRef(id="top_customers_by_revenue", params={"n": 2, "year": 2012})
    assert request.decomposition is None and request.plan is None
    assert request.datasource_id == "shop" and "SUM(il.UnitPrice * il.Quantity)" in request.sql
    assert out["errors"][0].error_code == ErrorCode.NEEDS_APPROVAL
    assert not out.get("artifact_refs")


def test_approved_verified_query_runs_its_pinned_values_with_no_matching_call(db):
    from nl2sql.pipeline.pinned import pinned_from_approvals

    first, _ = _verified(db, _runner(NEEDS))
    [request] = first["approvals_needed"]
    approvals = [Approval(request=request, by=DBA)]
    pinned = pinned_from_approvals(approvals, question=QUESTION, user=ANA, hooks=_runner(NEEDS))

    out, server = _verified(db, _runner(NEEDS), pinned=pinned, approvals=approvals)

    assert server.calls == []  # no candidates search, no choice call
    assert out["verified_match"].params == {"n": 2, "year": 2012}
    assert next(iter(out["artifact_refs"].values())).row_count == 2


# ---------------------------------------------------------------- the review's attacks (PR #319)


def test_the_sql_shown_to_the_approver_is_the_sql_whose_hash_is_checked():
    # A relay swaps the displayed SQL for something harmless, keeps the real hash, recomputes the id.
    swapped = _request().model_copy(update={"sql": "SELECT 1 -- harmless"})
    swapped = swapped.model_copy(update={"id": swapped.expected_id()})

    assert not _call(_runner(NEEDS), [_approval(swapped)]).allowed
    from nl2sql.hooks.approval import request_problem
    assert request_problem(_approval(swapped), question="How many customers?", user=ANA) == \
        "its SQL does not match its hash"


# A request as it was made by this version of the engine. The id hashes a fixed list of fields, each nested
# model without the values it holds at their defaults, so a field added later with a default keeps old ids
# (a store keyed by id survives an upgrade). Change this only when the hashed content changes on purpose.
GOLDEN_ID = "49dbee7ec7652582c469f3f6e4c78d64b3a90bd65a0c440835b536d4476d6a56"


def test_the_request_id_hashes_a_fixed_list_of_fields_without_their_defaults():
    from nl2sql.hooks.approval import REQUEST_ID_FIELDS

    request = _request()
    assert REQUEST_ID_FIELDS == ("question", "asker_user_id", "asker_tenant_id", "hook", "point", "datasource_id",
                                 "decomposition", "sub_query", "plan", "verified", "sql", "sql_sha256")
    # A nested value set explicitly to its default hashes as if it were left out.
    explicit = _request(sub_query=_sq().model_copy(update={"limit": None}),
                        plan=_plan().model_copy(update={"distinct": False}))
    assert explicit.id == request.id
    assert request.id == GOLDEN_ID
