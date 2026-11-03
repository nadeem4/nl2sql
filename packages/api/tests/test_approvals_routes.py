"""``/approvals``: the requests a ``needs_approval`` run stored, granted or denied by an approver, then named by the
asker's re-run.

The engine here is the real facade over a fake runtime with one hook whose approvers hold the role ``dba``.
"""
import pytest
from fastapi.testclient import TestClient

from nl2sql import NL2SQL, Approval, ApprovalRequest, ErrorCode, UserContext
from nl2sql.hooks import HookBinding, HookRunner
from nl2sql.storage.sqlite import SqliteState
from nl2sql.testing.runtime import fake_runtime
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

ANA = UserContext(user_id="ana", tenant_id="acme", roles=["analyst"])
BOB = UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])
DBA = UserContext(user_id="dba", tenant_id="acme", roles=["dba"])
SQL = "SELECT COUNT(t1.CustomerId) AS n FROM Customer AS t1"


def _request():
    return ApprovalRequest.build(question="How many customers?", asker=ANA, hook="review", datasource_id="chinook",
                                 sql=SQL, reason="Reads customers", approver_roles=["dba"])


@pytest.fixture
def served(tmp_path):
    state = SqliteState(tmp_path / "state.db")
    state.migrate()
    runner = HookRunner([HookBinding(name="review", hook=lambda ctx: None, points=frozenset({"execute.before"}),
                                     approver_roles=("dba",))], admin_roles=("admin",))
    engine = NL2SQL.__new__(NL2SQL)
    engine._runtime = fake_runtime(state=state, hooks=runner, closed=False)
    request = _request()
    engine.approvals.submit([request])
    who = {"user": ANA}
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[identity.caller] = lambda: who["user"]
    client = TestClient(app, raise_server_exceptions=False)
    yield client, request, who
    app.dependency_overrides.clear()
    client.close()
    state.close()


def test_an_asker_lists_their_own_and_an_approver_what_their_roles_may_approve(served):
    client, request, who = served

    [mine] = client.get("/api/v1/approvals").json()
    assert (mine["id"], mine["status"], mine["request"]["sql"]) == (request.id, "pending", SQL)
    assert "approver" not in mine, "who granted it, with the roles they held, is the engine's record"

    who["user"] = BOB
    assert client.get("/api/v1/approvals").json() == []
    who["user"] = DBA
    assert [row["id"] for row in client.get("/api/v1/approvals").json()] == [request.id]
    assert client.get("/api/v1/approvals", params={"status": "granted"}).json() == []
    assert client.get("/api/v1/approvals", params={"status": "maybe"}).status_code == 422


def test_approver_grants_and_asker_retries(served, monkeypatch):
    from nl2sql.api import query_api

    client, request, who = served
    seen = {}

    def fake_graph(ctx, question, **kwargs):
        seen.update(kwargs)
        return {"trace_id": "t-1", "user_query": question, "user_context": ANA}

    monkeypatch.setattr(query_api, "run_with_graph", fake_graph)

    assert client.post(f"/api/v1/approvals/{request.id}/grant").status_code == 403  # the asker may not approve
    who["user"] = DBA
    granted = client.post(f"/api/v1/approvals/{request.id}/grant")
    assert granted.status_code == 200, granted.text
    assert granted.json()["status"] == "granted"

    who["user"] = ANA
    answered = client.post("/api/v1/query", json={"question": "How many customers?", "approval_ids": [request.id]})
    assert answered.status_code == 200, answered.text
    assert [grant.request.id for grant in seen["approvals"]] == [request.id]


def test_an_ungranted_id_ends_the_run_approval_not_granted(served, monkeypatch):
    from nl2sql.api import query_api

    client, request, _ = served
    monkeypatch.setattr(query_api, "run_with_graph", lambda *a, **k: pytest.fail("the graph must not run"))

    response = client.post("/api/v1/query", json={"question": "How many customers?", "approval_ids": [request.id]})

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["errors"][0]["error_code"] == ErrorCode.APPROVAL_NOT_GRANTED.value


def test_an_approver_denies_with_a_reason(served):
    client, request, who = served

    assert client.post(f"/api/v1/approvals/{request.id}/deny", json={"reason": "No."}).status_code == 403
    who["user"] = DBA
    denied = client.post(f"/api/v1/approvals/{request.id}/deny", json={"reason": "Not for customers."})

    assert denied.status_code == 200, denied.text
    assert denied.json()["status"] == "denied"


def test_a_denial_needs_no_body(served):
    client, request, who = served
    who["user"] = DBA

    assert client.post(f"/api/v1/approvals/{request.id}/deny").status_code == 200


@pytest.mark.parametrize("action", ["grant", "deny"])
def test_an_unknown_or_other_tenants_request_is_404(served, action):
    client, request, who = served
    who["user"] = DBA

    assert client.post(f"/api/v1/approvals/nope/{action}").status_code == 404
    who["user"] = UserContext(user_id="mallory", tenant_id="globex", roles=["dba"])
    assert client.post(f"/api/v1/approvals/{request.id}/{action}").status_code == 404
