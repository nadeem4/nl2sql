"""The saved-answers and dashboard-builder routes (docs/architecture/wave_d.md, section 18, row DB1c): thin over
``engine.answers`` and ``engine.dashboards``, each handler passing the caller. A recorder stands in for the engine."""
import threading
import time

import pytest
from dashboard_stubs import AT, Recorder, dashboard

from nl2sql import (LockHeld, AnswerLimitReached, AnswerNotFound, BuilderOptions, BuildReport, ChartPlan, LLMOverride,
                    MissingProviderKey, NoTiles, NotIndexed, NotSavable, QueryResult, SavedAnswer, SaveReport,
                    SkippedQuestion, TileProgress, TileProposal, UserContext, VerifiedRef)
from nl2sql_api import identity
from nl2sql_api.main import app

API = "/api/v1"
ADMIN = UserContext(roles=["admin"])  # what the conftest's api_client resolves every request to


@pytest.fixture
def stub(api_client):
    client, engine = api_client()
    engine.answers, engine.dashboards = Recorder(), Recorder()
    return client, engine.answers, engine.dashboards


def code(response):
    return response.json()["error"]["code"]


def wait_for(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"{API}/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        assert time.monotonic() < deadline, "the job did not finish"
        time.sleep(0.01)


def saved(answer_id="a1") -> SavedAnswer:
    return SavedAnswer(id=answer_id, user_id="ann", question="Revenue by month?", datasource_id="chinook",
                       plan=ChartPlan(verified=VerifiedRef(id="v_revenue"), verified_datasource_id="chinook"),
                       created_at=AT, trace_id="tr-1")


def proposal(**fields) -> TileProposal:
    return TileProposal(**{"source": "curated", "ref": "v_revenue", "question": "Revenue by month?", **fields})


def result() -> QueryResult:
    return QueryResult(status="success", question="Revenue by month?", sub_queries=[], errors=[])


STEP = {"turn": 1, "step": 1}


# ---- answers

def test_save_passes_the_caller_and_returns_the_report(stub):
    client, answers, _ = stub
    answers.returns["save"] = SaveReport(answer=saved(), proposed=True, proposal_id="p1")
    response = client.post(f"{API}/answers", json={"trace_id": "tr-1", "conversation_id": "c1", "step": STEP,
                                                   "note": "good"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]["id"] == "a1" and body["proposed"] is True and body["proposal_id"] == "p1"
    (args, kwargs), = answers.called("save")
    assert args == () and kwargs["user_context"] == ADMIN and kwargs["trace_id"] == "tr-1"
    assert kwargs["conversation_id"] == "c1" and kwargs["note"] == "good"
    assert kwargs["step"].turn == 1 and kwargs["step"].step == 1


def test_save_body_is_strict(stub):
    client, _, _ = stub
    assert client.post(f"{API}/answers", json={"trace_id": "t", "conversation_id": "c", "step": STEP,
                                               "extra": 1}).status_code == 422
    assert client.post(f"{API}/answers", json={"trace_id": "t"}).status_code == 422


@pytest.mark.parametrize("error, status, expected", [
    (NotSavable("That step kept no plan."), 422, "NOT_SAVABLE"),
    (AnswerLimitReached("You have 100 saved answers."), 409, "ANSWER_LIMIT_REACHED"),
])
def test_save_refusals_are_mapped(stub, error, status, expected):
    client, answers, _ = stub
    answers.returns["save"] = error
    response = client.post(f"{API}/answers", json={"trace_id": "t", "conversation_id": "c", "step": STEP})
    assert response.status_code == status and code(response) == expected


def test_list_answers_can_filter_by_datasource(stub):
    client, answers, _ = stub
    answers.returns["list"] = [saved()]
    assert client.get(f"{API}/answers").json()[0]["id"] == "a1"
    client.get(f"{API}/answers", params={"datasource_id": "chinook"})
    assert [kw for _, kw in answers.called("list")] == [{"user_context": ADMIN, "datasource_id": None},
                                                         {"user_context": ADMIN, "datasource_id": "chinook"}]


def test_get_and_delete_an_answer(stub):
    client, answers, _ = stub
    answers.returns["get"] = saved()
    assert client.get(f"{API}/answers/a1").json()["question"] == "Revenue by month?"
    assert answers.called("get") == [(("a1",), {"user_context": ADMIN})]
    response = client.delete(f"{API}/answers/a1")
    assert response.status_code == 204 and response.content == b""
    assert answers.called("delete") == [(("a1",), {"user_context": ADMIN})]


def test_another_users_answer_is_404(stub):
    client, answers, _ = stub
    answers.returns["get"] = answers.returns["delete"] = answers.returns["run"] = AnswerNotFound("No answer 'a9'.")
    for response in (client.get(f"{API}/answers/a9"), client.delete(f"{API}/answers/a9"),
                     client.post(f"{API}/answers/a9/run")):
        assert response.status_code == 404 and code(response) == "ANSWER_NOT_FOUND"


def test_run_an_answer_with_approvals(stub):
    client, answers, _ = stub
    answers.returns["run"] = result()
    assert client.post(f"{API}/answers/a1/run").status_code == 200
    assert client.post(f"{API}/answers/a1/run", json={"approval_ids": ["r1"]}).status_code == 200
    (_, first), (_, second) = answers.called("run")
    assert first == {"user_context": ADMIN, "llm_override": None, "approvals": None}
    assert second["approvals"] == ["r1"]


def test_run_takes_a_request_key_as_other_routes_do(api_client):
    client, engine = api_client(api={"request_llm": True})
    engine.answers = Recorder()
    engine.answers.returns["run"] = result()
    client.post(f"{API}/answers/a1/run", headers={"X-NL2SQL-Api-Key-openai": "k" * 20})
    (_, kwargs), = engine.answers.called("run")
    assert isinstance(kwargs["llm_override"], LLMOverride)


# ---- builder options and proposals

def test_builder_options_for_a_datasource(stub):
    client, _, dashboards = stub
    dashboards.returns["builder_options"] = BuilderOptions()
    body = client.get(f"{API}/dashboard-builder/options", params={"datasource_id": "chinook"}).json()
    assert set(body) == {"curated", "saved", "metrics", "dimensions", "time_columns", "ranges"}
    assert dashboards.called("builder_options") == [(("chinook",), {"user_context": ADMIN})]
    assert client.get(f"{API}/dashboard-builder/options").status_code == 422


def test_builder_options_need_an_index(stub):
    client, _, dashboards = stub
    dashboards.returns["builder_options"] = NotIndexed("chinook has no index.")
    response = client.get(f"{API}/dashboard-builder/options", params={"datasource_id": "chinook"})
    assert response.status_code == 422 and code(response) == "NOT_INDEXED"


def test_proposals_pass_the_chips_and_return_the_tile_list(stub):
    client, _, dashboards = stub
    dashboards.returns["propose_tiles"] = [proposal()]
    response = client.post(f"{API}/dashboard-builder/proposals", json={
        "datasource_id": "chinook", "sources": ["curated", "asked"], "curated_ids": ["v_revenue"],
        "saved_ids": ["a1"], "metrics": ["revenue"], "dimensions": ["chinook.main.customers.country"],
        "time_range": "last_30_days", "text": "by country"})
    assert response.status_code == 200 and response.json()[0]["ref"] == "v_revenue"
    (args, kwargs), = dashboards.called("propose_tiles")
    assert args == ("chinook",) and kwargs["user_context"] == ADMIN and kwargs["sources"] == ["curated", "asked"]
    assert kwargs["curated_ids"] == ["v_revenue"] and kwargs["saved_ids"] == ["a1"]
    assert kwargs["metrics"] == ["revenue"] and kwargs["dimensions"] == ["chinook.main.customers.country"]
    assert kwargs["time_range"] == "last_30_days" and kwargs["text"] == "by country"
    assert kwargs["llm_override"] is None


def test_proposals_default_the_chips_to_empty(stub):
    client, _, dashboards = stub
    dashboards.returns["propose_tiles"] = []
    client.post(f"{API}/dashboard-builder/proposals", json={"datasource_id": "chinook", "sources": ["saved"]})
    (_, kwargs), = dashboards.called("propose_tiles")
    assert kwargs["curated_ids"] == [] and kwargs["metrics"] == [] and kwargs["time_range"] is None


def test_proposals_body_is_strict(stub):
    client, _, _ = stub
    for body in ({"sources": ["curated"]}, {"datasource_id": "c"}, {"datasource_id": "c", "sources": ["nope"]},
                 {"datasource_id": "c", "sources": []},
                 {"datasource_id": "c", "sources": ["curated"], "extra": 1}):
        assert client.post(f"{API}/dashboard-builder/proposals", json=body).status_code == 422


def test_asked_without_a_key_is_the_missing_key_error(stub):
    client, _, dashboards = stub
    dashboards.returns["propose_tiles"] = MissingProviderKey("conversation", "anthropic")
    response = client.post(f"{API}/dashboard-builder/proposals",
                           json={"datasource_id": "chinook", "sources": ["asked"]})
    assert response.status_code == 422 and code(response) == "MISSING_PROVIDER_KEY"


# ---- the build job

BUILD = {"datasource_id": "chinook", "tiles": [{"source": "curated", "ref": "v_revenue",
                                                 "question": "Revenue by month?"}],
         "title": "Sales", "workspace_id": "w1", "time_column": "chinook.main.invoices.date"}


def test_build_is_a_job_with_tile_progress(stub):
    client, _, dashboards = stub
    release = threading.Event()

    def build(datasource_id, tiles, **kwargs):
        kwargs["on_tile"](TileProgress(title="Revenue", status="ok", done=1, total=2))
        release.wait(5)
        kwargs["on_tile"](TileProgress(title="Gone", status="skipped", done=2, total=2))
        return BuildReport(dashboard=dashboard(), built=["t1"],
                           skipped=[SkippedQuestion(question="Gone?", code="PLAN_INVALID", message="no")])

    dashboards.returns["build"] = build
    response = client.post(f"{API}/jobs/dashboard-build", json=BUILD)
    assert response.status_code == 202
    job = response.json()
    assert job["kind"] == "dashboard-build" and job["params"]["datasource_id"] == "chinook"
    deadline = time.monotonic() + 5
    while not client.get(f"{API}/jobs/{job['id']}").json()["progress"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    release.set()
    done = wait_for(client, job["id"])
    assert done["status"] == "succeeded"
    assert [(p["step"], p["done"], p["total"]) for p in done["progress"]] == [("tile 1 of 2", 1, 2),
                                                                             ("tile 2 of 2", 2, 2)]
    assert done["result"]["dashboard"]["id"] == "d1" and done["result"]["built"] == ["t1"]
    assert done["result"]["skipped"][0]["code"] == "PLAN_INVALID"
    (args, kwargs), = dashboards.called("build")
    assert args[0] == "chinook" and args[1][0].ref == "v_revenue"
    assert kwargs["user_context"] == ADMIN and kwargs["title"] == "Sales" and kwargs["workspace_id"] == "w1"
    assert kwargs["time_column"] == "chinook.main.invoices.date" and kwargs["cancellation_token"] is not None
    assert kwargs["llm_override"] is None


def test_build_failure_is_the_jobs_error(stub):
    client, _, dashboards = stub
    dashboards.returns["build"] = NoTiles("None of the tiles could be built.")
    job = wait_for(client, client.post(f"{API}/jobs/dashboard-build", json=BUILD).json()["id"])
    assert job["status"] == "failed" and job["error"]["code"] == "NO_TILES"


def test_build_passes_a_request_key(api_client):
    client, engine = api_client(api={"request_llm": True})
    engine.dashboards = Recorder()
    engine.dashboards.returns["build"] = BuildReport(dashboard=dashboard(), built=["t1"])
    job = client.post(f"{API}/jobs/dashboard-build", json=BUILD, headers={"X-NL2SQL-Api-Key-openai": "k" * 20}).json()
    wait_for(client, job["id"])
    (_, kwargs), = engine.dashboards.called("build")
    assert isinstance(kwargs["llm_override"], LLMOverride)


def test_build_body_is_strict(stub):
    client, _, _ = stub
    assert client.post(f"{API}/jobs/dashboard-build", json={**BUILD, "extra": 1}).status_code == 422
    assert client.post(f"{API}/jobs/dashboard-build", json={"datasource_id": "chinook"}).status_code == 422


def test_a_second_build_while_one_runs_is_409(stub):
    client, _, dashboards = stub
    release = threading.Event()
    dashboards.returns["build"] = lambda *a, **k: (release.wait(5), BuildReport(dashboard=dashboard(), built=[]))[1]
    first = client.post(f"{API}/jobs/dashboard-build", json=BUILD)
    assert first.status_code == 202
    assert client.post(f"{API}/jobs/dashboard-build", json=BUILD).status_code == 409
    release.set()
    wait_for(client, first.json()["id"])


def test_build_result_is_the_owners_alone(api_client):
    client, engine = api_client(authenticated=False)
    engine.dashboards = Recorder()
    engine.dashboards.returns["build"] = BuildReport(dashboard=dashboard(), built=["t1"])
    app.dependency_overrides[identity.caller] = lambda: UserContext(user_id="ann", roles=["analyst"])
    job = client.post(f"{API}/jobs/dashboard-build", json=BUILD).json()
    wait_for(client, job["id"])
    app.dependency_overrides[identity.caller] = lambda: UserContext(user_id="bob", roles=["analyst"])
    assert client.get(f"{API}/jobs/{job['id']}").status_code == 404


def test_two_saves_racing_for_one_owner_is_a_retryable_409(stub):
    client, answers, _ = stub
    answers.returns["save"] = LockHeld("'answers:ann' is held by another process.")
    response = client.post(f"{API}/answers", json={"trace_id": "t", "conversation_id": "c", "step": STEP})
    assert response.status_code == 409 and code(response) == "CONFLICT"
    assert response.json()["error"]["fix"] == "Try again in a moment."
    assert "held by another" not in response.text


def test_a_new_question_without_a_key_fails_the_build_job_with_the_missing_key_code(stub):
    client, _, dashboards = stub
    dashboards.returns["build"] = MissingProviderKey("conversation", "anthropic")
    job = wait_for(client, client.post(f"{API}/jobs/dashboard-build", json=BUILD).json()["id"])
    assert job["status"] == "failed" and job["error"]["code"] == "MISSING_PROVIDER_KEY"


def test_skipped_questions_carry_the_engines_code_through(stub):
    client, _, dashboards = stub
    dashboards.returns["build"] = BuildReport(
        dashboard=dashboard(), built=["t1"],
        skipped=[SkippedQuestion(question="Payroll?", code="SECURITY_VIOLATION", message="You may not read this.")])
    job = wait_for(client, client.post(f"{API}/jobs/dashboard-build", json=BUILD).json()["id"])
    assert job["result"]["skipped"][0]["code"] == "SECURITY_VIOLATION"
