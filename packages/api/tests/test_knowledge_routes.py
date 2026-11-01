"""The knowledge and proposals routes (docs/architecture/wave_d.md, row A3): thin over ``engine.knowledge`` and
``engine.proposals``, each passing the caller. A stub engine records the calls; the models are the engine's own."""
import pytest
from dashboard_stubs import AT, Recorder

from nl2sql import (AcceptReport, ClarificationChoice, DatasourceKnowledgeView, DatasourceNotFound, KnowledgeSourceInfo,
                    NotAuthorized, Proposal, ReviewError, UserContext)

API = "/api/v1"
ADMIN = UserContext(roles=["admin"])


@pytest.fixture
def stub(api_client):
    client, engine = api_client()
    engine.knowledge, engine.proposals = Recorder(), Recorder()
    return client, engine.knowledge, engine.proposals


def proposal(proposal_id="p1", kind="term", **changes):
    return Proposal(id=proposal_id, kind=kind, datasource_id="chinook", target="revenue",
                    change={"definition": "Sum of invoice totals"}, origin="clarification", created_at=AT, **changes)


# ---- knowledge

def test_knowledge_show_as_caller(stub):
    client, knowledge, _ = stub
    knowledge.returns["show"] = [DatasourceKnowledgeView(datasource_id="chinook", problems=["Unknown table X"])]

    body = client.get(f"{API}/knowledge", params={"datasource_id": "chinook"}).json()

    assert body[0]["datasource_id"] == "chinook" and body[0]["problems"] == ["Unknown table X"]
    assert knowledge.called("show") == [(("chinook",), {"user_context": ADMIN})]
    client.get(f"{API}/knowledge")
    assert knowledge.called("show")[-1] == ((None,), {"user_context": ADMIN})


def test_knowledge_of_an_unknown_datasource_is_a_404(stub):
    client, knowledge, _ = stub
    knowledge.returns["show"] = DatasourceNotFound("Unknown datasource ID: nope")
    assert client.get(f"{API}/knowledge", params={"datasource_id": "nope"}).status_code == 404


def test_knowledge_sources_as_caller(stub):
    client, knowledge, _ = stub
    knowledge.returns["sources"] = [KnowledgeSourceInfo(type="yaml", counts={"chinook": {"term": 2}})]

    body = client.get(f"{API}/knowledge/sources").json()

    assert body[0]["type"] == "yaml" and body[0]["counts"] == {"chinook": {"term": 2}}
    assert knowledge.called("sources") == [((), {"user_context": ADMIN})]


def test_reload_is_admin_and_returns_the_sources(stub):
    client, knowledge, _ = stub
    knowledge.returns["reload"] = [KnowledgeSourceInfo(type="yaml")]
    assert client.post(f"{API}/knowledge/reload").json()[0]["type"] == "yaml"
    assert knowledge.called("reload") == [((), {"user_context": ADMIN})]


def test_save_choice_creates_a_term_proposal(stub):
    client, knowledge, _ = stub
    knowledge.returns["save_choice"] = proposal("p9")
    step = {"datasource_id": "chinook", "source": "chosen", "text": "Total of invoices"}
    option = lambda option_id: {"id": option_id, "label": f"Reading {option_id}", "plan": {}, "explanation": step}
    choice = ClarificationChoice.model_validate({
        "request": {"id": "r1", "question": "Which revenue?", "phrase": "revenue", "datasource_id": "chinook",
                    "schema_version": "v1", "knowledge_digest": "k1", "sub_query": {},
                    "options": [option("a"), option("b")]},
        "option_id": "a"})

    response = client.post(f"{API}/knowledge/choices", json={"choice": choice.model_dump(mode="json"),
                                                             "term": "revenue"})

    assert response.status_code == 200, response.text
    assert response.json()["id"] == "p9" and response.json()["kind"] == "term"
    (args, kwargs), = knowledge.called("save_choice")
    assert args[0] == choice and kwargs == {"user_context": ADMIN, "term": "revenue"}


def test_save_choice_body_is_strict(stub):
    client, _, _ = stub
    assert client.post(f"{API}/knowledge/choices", json={}).status_code == 422
    assert client.post(f"{API}/knowledge/choices", json={"choice": {}, "extra": 1}).status_code == 422


# ---- proposals

def test_proposals_list_and_get_pass_the_filters_and_the_caller(stub):
    client, _, proposals = stub
    proposals.returns["list"] = [proposal()]
    proposals.returns["get"] = proposal("p1")

    body = client.get(f"{API}/proposals", params={"kind": "term", "status": "proposed", "datasource_id": "chinook",
                                                  "limit": 5}).json()

    assert [p["id"] for p in body] == ["p1"]
    assert proposals.called("list") == [((), {"user_context": ADMIN, "kind": "term", "status": "proposed",
                                              "datasource_id": "chinook", "limit": 5})]
    client.get(f"{API}/proposals")
    assert proposals.called("list")[-1][1]["status"] == "proposed" and proposals.called("list")[-1][1]["kind"] is None
    assert client.get(f"{API}/proposals/p1").json()["id"] == "p1"
    assert proposals.called("get") == [(("p1",), {"user_context": ADMIN})]


def test_a_proposal_that_is_not_there_is_a_404(stub):
    client, _, proposals = stub
    proposals.returns["get"] = ReviewError("NOT_FOUND", "No such proposal.")
    response = client.get(f"{API}/proposals/zz")
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


def test_accept_many_with_edits(stub):
    client, _, proposals = stub
    proposals.returns["accept"] = AcceptReport(accepted=[proposal("p1"), proposal("p2")],
                                               files_written=["glossary.yaml"], refused={"p3": "no longer applies"})

    response = client.post(f"{API}/proposals/accept", json={
        "ids": ["p1", "p2", "p3"], "edits": {"p1": {"definition": "Net of tax"}}})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["id"] for p in body["accepted"]] == ["p1", "p2"] and body["refused"] == {"p3": "no longer applies"}
    assert proposals.called("accept") == [((["p1", "p2", "p3"],), {"by": ADMIN,
                                                                   "edits": {"p1": {"definition": "Net of tax"}}})]
    client.post(f"{API}/proposals/accept", json={"ids": ["p1"]})
    assert proposals.called("accept")[-1][1]["edits"] is None


def test_reject_with_a_reason(stub):
    client, _, proposals = stub
    proposals.returns["reject"] = [proposal("p1", status="rejected")]

    response = client.post(f"{API}/proposals/reject", json={"ids": ["p1"], "reason": "wrong"})

    assert response.json()[0]["status"] == "rejected"
    assert proposals.called("reject") == [((["p1"],), {"by": ADMIN, "reason": "wrong"})]


def test_accept_and_reject_need_admin(api_client):
    client, engine = api_client(authenticated=False, api={"auth": "static", "static_role": "analyst"})
    engine.proposals = Recorder()

    for path in ("accept", "reject"):
        assert client.post(f"{API}/proposals/{path}", json={"ids": ["p1"]}).status_code == 403
    assert client.get(f"{API}/proposals").status_code == 403
    assert client.get(f"{API}/proposals/p1").status_code == 403
    assert engine.proposals.calls == []


def test_a_facade_refusal_for_a_caller_without_the_role_is_a_403(stub):
    client, _, proposals = stub
    proposals.returns["accept"] = NotAuthorized("Deciding needs an admin role.")
    assert client.post(f"{API}/proposals/accept", json={"ids": ["p1"]}).status_code == 403


def test_accept_file_changed_is_409(stub):
    client, _, proposals = stub
    proposals.returns["accept"] = ReviewError("FILE_CHANGED",
                                              "glossary.yaml changed on disk since the engine loaded it.")

    response = client.post(f"{API}/proposals/accept", json={"ids": ["p1"]})

    assert response.status_code == 409 and response.json()["error"]["code"] == "FILE_CHANGED"


def test_file_starter_questions_as_examples(stub):
    client, _, proposals = stub
    proposals.returns["propose_examples"] = [proposal("e1", kind="example")]

    response = client.post(f"{API}/proposals/examples", json={"datasource_id": "chinook",
                                                              "questions": ["How many tracks?", "Top artists?"]})

    assert response.status_code == 200, response.text
    assert [p["id"] for p in response.json()] == ["e1"]
    assert proposals.called("propose_examples") == [(("chinook", ["How many tracks?", "Top artists?"]),
                                                     {"by": ADMIN})]


def test_proposal_bodies_are_strict(stub):
    client, _, _ = stub
    assert client.post(f"{API}/proposals/accept", json={}).status_code == 422
    assert client.post(f"{API}/proposals/accept", json={"ids": ["p1"], "nope": 1}).status_code == 422
    assert client.post(f"{API}/proposals/reject", json={"ids": "p1"}).status_code == 422
    assert client.post(f"{API}/proposals/examples", json={"datasource_id": "chinook"}).status_code == 422
