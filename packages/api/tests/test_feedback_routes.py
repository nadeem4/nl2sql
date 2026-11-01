"""``/traces``, ``/feedback`` and ``/feedback/correct``: a run's trace and its ratings, bound to the caller who asked it.

The engine here is the real facade over a fake runtime holding runs of ``ann``; ``bob`` is another caller of the same
tenant. A REST caller is always a scoped user: no route reads a run as the trusted in-process operator, so another
caller's run is a 404 whichever route asks for it.
"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from nl2sql import NL2SQL, Proposal, UserContext
from nl2sql.execution.artifacts.results import ResultOwner
from nl2sql.feedback.corrections import CorrectionRefused
from nl2sql.storage.sqlite import SqliteState
from nl2sql.testing.runtime import fake_runtime
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

ANN = UserContext(user_id="ann", tenant_id="acme", roles=["analyst"])
BOB = UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])
ADMIN = UserContext(user_id="root", tenant_id="acme", roles=["admin"])
QUESTION = "How many customers?"


def _trace(trace_id, owner):
    return {"trace_format_version": 1, "trace_id": trace_id, "nodes": [], "owner": owner,
            "request": {"question": QUESTION, "roles": ["analyst"]},
            "result": {"trace_id": trace_id, "status": "error", "source": {"type": "generated"},
                       "sub_queries": [{"id": "sq1", "sql": "SELECT 1", "datasource_id": "shop"}]}}


@pytest.fixture
def served(tmp_path):
    """``(client, engine, who)``: the real facade over a fake runtime; ``who["user"]`` is the caller of the next request."""
    state = SqliteState(tmp_path / "state.db")
    state.migrate()
    engine = NL2SQL.__new__(NL2SQL)
    engine._runtime = fake_runtime(state=state, closed=False)
    ctx = engine._runtime
    ctx.traces.write("run-1", _trace("run-1", ResultOwner.of(ANN, ctx.tenant_id).model_dump()))
    who = {"user": ANN}
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[identity.caller] = lambda: who["user"]
    client = TestClient(app, raise_server_exceptions=False)
    yield client, engine, who
    app.dependency_overrides.clear()
    client.close()
    state.close()


def test_the_asker_reads_their_trace(served):
    client, _, _ = served

    response = client.get("/api/v1/traces/run-1")

    assert response.status_code == 200, response.text
    assert response.json()["trace_id"] == "run-1" and response.json()["request"]["question"] == QUESTION


@pytest.mark.parametrize("path", ["/api/v1/traces/nope", "/api/v1/traces/..%2Frun-1", "/api/v1/traces/run-1%00"])
def test_an_unknown_or_crafted_trace_id_is_404(served, path):
    client, _, _ = served

    response = client.get(path)

    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


def test_trace_route_hides_another_users_trace(served):
    client, _, who = served
    who["user"] = BOB

    response = client.get("/api/v1/traces/run-1")

    assert response.status_code == 404
    assert QUESTION not in response.text


def test_feedback_records_from_the_trace(served):
    client, engine, _ = served

    response = client.post("/api/v1/feedback", json={"trace_id": "run-1", "rating": "down", "note": "wrong table"})

    assert response.status_code == 200, response.text
    [row] = engine.feedback.list()
    assert (row["trace_id"], row["rating"], row["question"], row["note"]) == ("run-1", "down", QUESTION, "wrong table")
    assert response.json()["trace_id"] == "run-1"


def test_feedback_of_another_users_run_is_404_and_stores_nothing(served):
    client, engine, who = served
    who["user"] = BOB

    response = client.post("/api/v1/feedback", json={"trace_id": "run-1", "rating": "down"})

    assert response.status_code == 404
    assert engine.feedback.list() == []


@pytest.mark.parametrize("body", [{"trace_id": "run-1", "rating": "sideways"}, {"rating": "up"},
                                  {"trace_id": "run-1", "rating": "up", "question": "x"}])
def test_a_bad_feedback_body_is_422(served, body):
    client, engine, _ = served

    assert client.post("/api/v1/feedback", json=body).status_code == 422
    assert engine.feedback.list() == []


def test_listing_the_ratings_is_admin_work(served):
    client, engine, who = served
    engine.feedback.record("run-1", "down", user_context=ANN)

    assert client.get("/api/v1/feedback").status_code == 403
    who["user"] = ADMIN
    response = client.get("/api/v1/feedback", params={"limit": 5})
    assert response.status_code == 200 and [row["trace_id"] for row in response.json()] == ["run-1"]
    assert client.get("/api/v1/feedback", params={"limit": 0}).status_code == 422


def _proposal():
    return Proposal.build(kind="verified_query", datasource_id="shop", target=QUESTION,
                          change={"questions": [QUESTION], "sql": "SELECT COUNT(*) FROM customers"},
                          origin="correction", tables=["shop.public.customers"], evidence={"trace_id": "run-1"})


class _Feedback:
    """Stands in for ``engine.feedback``: records how a route calls it."""

    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def correct(self, trace_id, **kwargs):
        self.calls.append({"trace_id": trace_id, **kwargs})
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_correct_returns_a_proposal(api_client):
    client, engine = api_client()
    engine.feedback = _Feedback(_proposal())

    response = client.post("/api/v1/feedback/correct", json={"trace_id": "run-1", "sql": "SELECT COUNT(*) FROM customers",
                                                             "question": QUESTION, "note": "count them"})

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "verified_query" and response.json()["origin"] == "correction"
    [call] = engine.feedback.calls
    assert (call["trace_id"], call["sql"], call["plan"], call["question"], call["note"]) == (
        "run-1", "SELECT COUNT(*) FROM customers", None, QUESTION, "count them")


def test_correct_never_acts_as_the_operator(api_client):
    client, engine = api_client()
    engine.feedback = _Feedback(_proposal())

    client.post("/api/v1/feedback/correct", json={"trace_id": "run-1", "plan": {"tables": []}})

    [call] = engine.feedback.calls
    assert "as_operator" not in call, "a REST caller is always a scoped user; the operator path is the CLI's"
    source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "nl2sql_api").rglob("*.py")
    assert not [str(path) for path in source if "as_operator" in path.read_text(encoding="utf-8")]


def test_correct_of_another_users_run_is_404_and_rates_nothing(served):
    client, engine, who = served
    who["user"] = BOB

    response = client.post("/api/v1/feedback/correct", json={"trace_id": "run-1", "sql": "SELECT 1"})

    assert response.status_code == 404
    assert engine.feedback.list() == []


def test_a_refused_correction_is_422_with_its_reasons(api_client):
    client, engine = api_client()
    engine.feedback = _Feedback(CorrectionRefused("not_read_only", "The fix is not a read-only query.",
                                                  ["Only SELECT is allowed."]))

    response = client.post("/api/v1/feedback/correct", json={"trace_id": "run-1", "sql": "DROP TABLE x"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "CORRECTION_REFUSED" and error["message"] == "The fix is not a read-only query."
    assert error["details"] == [{"code": "not_read_only", "msg": "Only SELECT is allowed."}]


def test_a_correction_needs_exactly_one_fix(api_client):
    client, engine = api_client()
    engine.feedback = _Feedback(_proposal())

    for body in ({"trace_id": "run-1"}, {"trace_id": "run-1", "sql": "SELECT 1", "plan": {}}):
        response = client.post("/api/v1/feedback/correct", json=body)
        assert response.status_code == 422, body
    assert engine.feedback.calls == []
