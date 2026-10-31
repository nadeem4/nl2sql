"""Stored approval grants (docs/architecture/wave_d.md, D215 and D236): ``engine.approvals`` and request ids."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nl2sql.approvals.api import Approvals
from nl2sql.common.errors import ApprovalNotFound, ErrorCode, ErrorSeverity, NotAuthorized, PipelineError
from nl2sql.hooks import HookBinding, HookRunner
from nl2sql.hooks.approval import Approval, ApprovalRequest
from nl2sql.pipeline.state import GraphState
from nl2sql.policies import UserContext
from nl2sql.storage.sqlite import SqliteState
from nl2sql.testing.runtime import fake_runtime

ANA = UserContext(user_id="ana", tenant_id="acme", roles=["analyst"])
BOB = UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])
DBA = UserContext(user_id="dba", tenant_id="acme", roles=["dba"])
SQL = "SELECT COUNT(t1.CustomerId) AS n FROM Customer AS t1"


def _request(**changes):
    fields = dict(question="How many customers?", asker=ANA, hook="review", datasource_id="chinook", sql=SQL,
                  reason="Reads customers", approver_roles=["dba"])
    fields.update(changes)
    return ApprovalRequest.build(**fields)


def _runner():
    return HookRunner([HookBinding(name="review", hook=lambda ctx: None, points=frozenset({"execute.before"}),
                                   approver_roles=("dba",))], admin_roles=("admin",))


def _age(state, request_id, hours=25):
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    state.put_approval({**state.get_approval(request_id), "updated_at": old})


@pytest.fixture
def state():
    store = SqliteState(":memory:")
    store.migrate()
    yield store
    store.close()


@pytest.fixture
def ctx(state):
    return fake_runtime(state=state, hooks=_runner())


@pytest.fixture
def approvals(ctx):
    return Approvals(ctx)


def test_needs_approval_run_stores_its_request(approvals):
    request = _request()
    approvals.submit([request])
    approvals.submit([request])  # the same re-run: still one pending request

    [record] = approvals.list(user_context=ANA)
    assert (record.id, record.status, record.request) == (request.id, "pending", request)


def test_a_run_stores_its_requests_through_the_facade(ctx, state, monkeypatch):
    from nl2sql.api import query_api

    request = _request()
    error = PipelineError(node="executor", message="needs approval", severity=ErrorSeverity.CRITICAL,
                          error_code=ErrorCode.NEEDS_APPROVAL)
    graph_state = GraphState(user_query="How many customers?", approvals_needed=[request], user_context=ANA,
                             errors=[error]).model_dump()
    monkeypatch.setattr(query_api, "run_with_graph", lambda *a, **k: graph_state)

    result = query_api.QueryAPI(ctx).run_query("How many customers?", user_context=ANA)

    assert result.status == "needs_approval"
    assert [r["id"] for r in state.list_approvals("pending")] == [request.id]


def test_grant_needs_an_approver_role(approvals):
    request = _request()
    approvals.submit([request])
    with pytest.raises(NotAuthorized):
        approvals.grant(request.id, by=BOB)
    with pytest.raises(ApprovalNotFound):
        approvals.grant("nope", by=DBA)

    record = approvals.grant(request.id, by=DBA)

    assert (record.status, record.decided_by) == ("granted", "dba")


def test_list_shows_an_approver_what_their_roles_may_approve_and_an_asker_their_own(approvals):
    mine, theirs = _request(), _request(asker=BOB)
    approvals.submit([mine, theirs])

    assert [r.id for r in approvals.list(user_context=ANA)] == [mine.id]
    assert sorted(r.id for r in approvals.list(user_context=DBA)) == sorted([mine.id, theirs.id])
    assert approvals.list(user_context=UserContext(user_id="eve", tenant_id="other", roles=["analyst"])) == []
    assert approvals.list(user_context=DBA, status="granted") == []


def test_grant_by_id_runs_as_an_approval(approvals):
    request = _request()
    approvals.submit([request])
    approvals.grant(request.id, by=DBA)

    resolved, problem = approvals.resolve([request.id], user=ANA)

    assert problem is None
    assert resolved == [Approval(request=request, by=DBA)]


def test_resolve_passes_approval_objects_through(approvals):
    approval = Approval(request=_request(), by=DBA)
    assert approvals.resolve([approval], user=ANA) == ([approval], None)


def test_denied_request_does_not_run(approvals):
    request = _request()
    approvals.submit([request])
    with pytest.raises(NotAuthorized):
        approvals.deny(request.id, by=BOB)
    record = approvals.deny(request.id, by=DBA, reason="No.")

    assert (record.status, record.reason) == ("denied", "No.")
    resolved, problem = approvals.resolve([request.id], user=ANA)
    assert resolved == [] and "denied" in problem


def test_unknown_and_pending_ids_do_not_run(approvals):
    request = _request()
    approvals.submit([request])
    assert "waiting" in approvals.resolve([request.id], user=ANA)[1]
    assert "no approval request" in approvals.resolve(["nope"], user=ANA)[1]


def test_grant_is_reusable_until_it_expires(approvals, state):
    request = _request()
    approvals.submit([request])
    approvals.grant(request.id, by=DBA)
    assert approvals.resolve([request.id], user=ANA)[1] is None
    assert approvals.resolve([request.id], user=ANA)[1] is None  # reusable

    _age(state, request.id)

    resolved, problem = approvals.resolve([request.id], user=ANA)
    assert resolved == [] and "expired" in problem


def test_an_expired_request_becomes_pending_again_when_the_run_asks_again(approvals, state):
    request = _request()
    approvals.submit([request])
    approvals.grant(request.id, by=DBA)
    _age(state, request.id)

    approvals.submit([request])

    assert state.get_approval(request.id)["status"] == "pending"


def test_grants_are_purged(state):
    from types import SimpleNamespace

    from nl2sql.api.storage_api import StorageAPI

    ctx = fake_runtime(state=state, hooks=_runner())
    ctx.results = SimpleNamespace()  # no max_age_days: not purged
    request = _request()
    Approvals(ctx).submit([request])
    _age(state, request.id)

    counts = StorageAPI(ctx).purge()

    assert counts["approvals"] == 1 and state.get_approval(request.id) is None


def test_a_run_with_an_ungranted_id_ends_approval_not_granted(ctx, monkeypatch):
    from nl2sql.api import query_api

    def boom(*a, **k):
        raise AssertionError("the graph must not run")

    monkeypatch.setattr(query_api, "run_with_graph", boom)

    result = query_api.QueryAPI(ctx).run_query("How many customers?", user_context=ANA, approvals=["nope"])

    assert result.status == "error"
    assert result.errors[0]["error_code"] == ErrorCode.APPROVAL_NOT_GRANTED.value


def test_a_run_with_a_granted_id_passes_the_approval_to_the_graph(ctx, approvals, monkeypatch):
    from nl2sql.api import query_api

    request = _request()
    approvals.submit([request])
    approvals.grant(request.id, by=DBA)
    seen = {}

    def fake_graph(ctx_, question, **kwargs):
        seen.update(kwargs)
        return {"trace_id": "t", "user_query": question, "user_context": ANA}

    monkeypatch.setattr(query_api, "run_with_graph", fake_graph)

    query_api.QueryAPI(ctx).run_query("How many customers?", user_context=ANA, approvals=[request.id])

    assert seen["approvals"] == [Approval(request=request, by=DBA)]


def test_refresh_with_a_granted_id(ctx, approvals):
    from nl2sql.api.dashboards_api import resolve_refresh_approvals

    request = _request()
    approvals.submit([request])
    with pytest.raises(ApprovalNotFound):
        resolve_refresh_approvals(ctx, [request.id], ANA)  # still pending
    approvals.grant(request.id, by=DBA)

    assert resolve_refresh_approvals(ctx, [request.id], ANA) == [Approval(request=request, by=DBA)]


# ---------------------------------------------------------------- the review's findings (PR #410)

MALLORY = UserContext(user_id="mallory", tenant_id="globex", roles=["dba"])


def test_another_tenants_request_is_not_listed_or_grantable(approvals):
    request = _request()
    approvals.submit([request])

    assert approvals.list(user_context=MALLORY) == []
    with pytest.raises(ApprovalNotFound):
        approvals.grant(request.id, by=MALLORY)
    with pytest.raises(ApprovalNotFound):
        approvals.deny(request.id, by=MALLORY)


def test_another_tenants_id_does_not_resolve(approvals):
    request = _request()
    approvals.submit([request])
    approvals.grant(request.id, by=DBA)

    resolved, problem = approvals.resolve([request.id], user=UserContext(user_id="ana", tenant_id="globex"))

    assert resolved == [] and "no approval request" in problem


def test_callers_with_no_identity_do_not_see_each_others_requests(approvals):
    nobody = UserContext(roles=["viewer"])
    approvals.submit([_request(asker=UserContext(roles=["admin"]))])

    assert approvals.list(user_context=nobody) == []


def test_a_purged_request_is_not_found_when_decided(approvals, state, monkeypatch):
    request = _request()
    approvals.submit([request])
    monkeypatch.setattr(state, "decide_approval", lambda *a, **k: None)

    with pytest.raises(ApprovalNotFound):
        approvals.grant(request.id, by=DBA)
    with pytest.raises(ApprovalNotFound):
        approvals.deny(request.id, by=DBA)


def test_a_grant_with_no_approver_is_waiting_until_an_approver_grants_again(approvals, state):
    request = _request()
    approvals.submit([request])
    state.decide_approval(request.id, "granted", by="dba")  # the crash between the two writes
    assert "waiting" in approvals.resolve([request.id], user=ANA)[1]

    other = UserContext(user_id="dba2", tenant_id="acme", roles=["dba"])
    approvals.grant(request.id, by=other)

    resolved, problem = approvals.resolve([request.id], user=ANA)
    assert problem is None and resolved[0].by == other
    assert state.get_approval(request.id)["decided_by"] == "dba2"


def test_deny_then_grant_stays_denied(approvals):
    request = _request()
    approvals.submit([request])
    approvals.deny(request.id, by=DBA)

    assert approvals.grant(request.id, by=DBA).status == "denied"
    assert "denied" in approvals.resolve([request.id], user=ANA)[1]


def test_rerun_does_not_reopen_a_denied_request(approvals, state):
    request = _request()
    approvals.submit([request])
    approvals.deny(request.id, by=DBA)

    approvals.submit([request])

    assert state.get_approval(request.id)["status"] == "denied"


def test_the_approver_is_not_part_of_what_callers_see(approvals):
    request = _request()
    approvals.submit([request])
    approvals.grant(request.id, by=DBA)

    [record] = approvals.list(user_context=ANA, status="granted")

    assert record.decided_by == "dba" and "approver" not in record.model_dump()


def _stream_state(request):
    error = PipelineError(node="executor", message="needs approval", severity=ErrorSeverity.CRITICAL,
                          error_code=ErrorCode.NEEDS_APPROVAL)
    return GraphState(user_query="How many customers?", approvals_needed=[request], user_context=ANA,
                      errors=[error]).model_dump()


def test_stream_stores_its_requests(ctx, state, monkeypatch):
    from nl2sql.api import query_api

    request = _request()
    monkeypatch.setattr(query_api, "run_with_graph", lambda *a, **k: _stream_state(request))

    events = list(query_api.QueryAPI(ctx).stream_query("How many customers?", user_context=ANA))

    assert events[-1].name == "run.finished"
    assert [r["id"] for r in state.list_approvals("pending")] == [request.id]


def test_stream_with_an_ungranted_id_ends_approval_not_granted(ctx, monkeypatch):
    from nl2sql.api import query_api

    monkeypatch.setattr(query_api, "run_with_graph", lambda *a, **k: pytest.fail("the graph must not run"))

    events = list(query_api.QueryAPI(ctx).stream_query("How many customers?", user_context=ANA, approvals=["nope"]))

    result = events[-1].data["result"]
    assert result["status"] == "error" and result["errors"][0]["error_code"] == "APPROVAL_NOT_GRANTED"


def test_stream_with_a_granted_id_passes_the_approval(ctx, approvals, monkeypatch):
    from nl2sql.api import query_api

    request = _request()
    approvals.submit([request])
    approvals.grant(request.id, by=DBA)
    seen = {}

    def graph(ctx_, question, **kwargs):
        seen.update(kwargs)
        return {"trace_id": "t", "user_query": question, "user_context": ANA}

    monkeypatch.setattr(query_api, "run_with_graph", graph)

    list(query_api.QueryAPI(ctx).stream_query("How many customers?", user_context=ANA, approvals=[request.id]))

    assert seen["approvals"] == [Approval(request=request, by=DBA)]


def test_refresh_facade_raises_before_any_chart_runs(ctx):
    from types import SimpleNamespace

    from nl2sql.api.dashboards_api import DashboardsAPI

    api = DashboardsAPI.__new__(DashboardsAPI)
    api._ctx = ctx
    api._service = SimpleNamespace(refresh=lambda *a, **k: pytest.fail("no chart may run"))

    with pytest.raises(ApprovalNotFound):
        api.refresh("d1", user_context=ANA, approvals=["nope"])
