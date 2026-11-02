"""The evals routes and the eval and demo jobs (docs/architecture/wave_d.md, row A4a). A stub engine records the calls and
answers with the engine's own models. Every route here is admin only."""
import pathlib
import time
from types import SimpleNamespace

import pytest
from dashboard_stubs import Recorder

from nl2sql import EvalReport, UserContext
from nl2sql.api.types import IndexProgress

API = "/api/v1"
ADMIN = UserContext(roles=["admin"])
CHINOOK = "evals/chinook.yaml"


def wait_for(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"{API}/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        assert time.monotonic() < deadline, "the job did not finish"
        time.sleep(0.01)


def suite(name="chinook", approved=2, drafts=1):
    cases = [SimpleNamespace(status="approved")] * approved + [SimpleNamespace(status="draft")] * drafts
    return SimpleNamespace(name=name, cases=cases, approved=lambda: cases[:approved])


@pytest.fixture
def stub(api_client):
    client, engine = api_client(evals={"suites": [CHINOOK]})
    engine.evals = Recorder()
    engine.add_demo = lambda **kwargs: None
    return client, engine


def test_suites_lists_each_configured_suite_with_its_case_counts(stub):
    client, engine = stub
    engine.evals.returns["load"] = [suite()]

    response = client.get(f"{API}/evals/suites")

    assert response.status_code == 200, response.text
    assert response.json() == [{"name": "chinook", "path": CHINOOK, "cases": 3, "approved": 2}]


def test_suites_is_empty_when_none_is_configured(api_client):
    client, engine = api_client()
    engine.evals = Recorder()

    assert client.get(f"{API}/evals/suites").json() == []
    assert engine.evals.calls == []


def test_scorers_lists_the_registered_names(stub):
    client, engine = stub
    engine.evals.returns["scorers"] = ["result_match", "outcome"]

    assert client.get(f"{API}/evals/scorers").json() == ["result_match", "outcome"]


def test_eval_job_runs_the_suites_and_reports_each_case(stub):
    client, engine = stub

    def run(suites, *, mode, cases, max_cost_usd, on_case, cancellation_token):
        on_case("default", {"id": "c1"}, 0.0)
        on_case("default", {"id": "c2"}, 0.0)
        return EvalReport(mode="plans")

    engine.evals.returns["run"] = run
    response = client.post(f"{API}/jobs/eval", json={"mode": "plans", "cases": ["c1", "c2"]})
    assert response.status_code == 202, response.text

    job = wait_for(client, response.json()["id"])

    assert job["status"] == "succeeded" and job["result"]["mode"] == "plans"
    assert [p["done"] for p in job["progress"]] == [1, 2] and job["progress"][-1]["step"] == "case c2"
    ((args, kwargs),) = engine.evals.called("run")
    assert args == ([pathlib.Path(CHINOOK)],)  # the configured suite, never a caller's path
    assert kwargs["mode"] == "plans" and kwargs["cases"] == ["c1", "c2"] and kwargs["cancellation_token"] is not None
    assert "llm" not in kwargs  # an eval runs on the server's model, never a caller's


def test_eval_job_is_admin_only(api_client):
    client, engine = api_client(authenticated=False, api={"auth": "static", "static_role": "analyst"},
                                evals={"suites": [CHINOOK]})
    engine.evals = Recorder()

    assert client.post(f"{API}/jobs/eval", json={"mode": "plans"}).status_code == 403
    assert client.get(f"{API}/evals/suites").status_code == 403
    assert client.get(f"{API}/evals/scorers").status_code == 403
    assert engine.evals.calls == []


def test_eval_job_refuses_a_suite_that_is_not_configured(stub):
    client, engine = stub

    response = client.post(f"{API}/jobs/eval", json={"suites": ["../../etc/secrets.yaml"], "mode": "plans"})

    assert response.status_code == 422, response.text
    assert "evals.suites" in response.json()["error"]["fix"]
    assert engine.evals.calls == []


def test_eval_job_names_a_suite_by_its_file_or_its_name(stub):
    client, engine = stub
    engine.evals.returns["run"] = lambda *a, **k: EvalReport(mode="plans")

    for name in (CHINOOK, "chinook"):
        response = client.post(f"{API}/jobs/eval", json={"suites": [name], "mode": "plans"})
        assert wait_for(client, response.json()["id"])["status"] == "succeeded"


def test_eval_job_in_mode_full_needs_a_cost_cap(stub):
    client, engine = stub

    response = client.post(f"{API}/jobs/eval", json={"mode": "full"})

    assert response.status_code == 422, response.text
    assert "max_cost_usd" in response.json()["error"]["message"]
    assert engine.evals.calls == []


def test_eval_job_in_mode_full_takes_a_cap_from_the_request_or_evals_yaml(api_client):
    client, engine = api_client(evals={"suites": [CHINOOK], "max_cost_usd": 2.0})
    engine.evals = Recorder()
    engine.evals.returns["run"] = lambda *a, **k: EvalReport(mode="full")

    first = client.post(f"{API}/jobs/eval", json={"mode": "full"})
    assert first.status_code == 202
    wait_for(client, first.json()["id"])
    assert client.post(f"{API}/jobs/eval", json={"mode": "full", "max_cost_usd": 0.5}).status_code == 202


@pytest.mark.parametrize("body", [{"mode": "plans", "max_cost_usd": 0}, {"mode": "plans", "llm": {}},
                                  {"mode": "other"}])
def test_eval_job_refuses_a_body_it_does_not_know(stub, body):
    client, engine = stub

    assert client.post(f"{API}/jobs/eval", json=body).status_code == 422
    assert engine.evals.calls == []


# ---- the demo job

def test_demo_job_adds_the_samples_and_indexes_them(stub):
    client, engine = stub
    seen = {}

    def add_demo(*, user_context=None, on_datasource=None):
        seen["user"] = user_context
        for name in ("chinook", "support", "web_analytics"):
            on_datasource(IndexProgress(datasource_id=name, state="started"))
            on_datasource(IndexProgress(datasource_id=name, state="finished"))
        return SimpleNamespace(ok=True, stats=[{"datasource_id": "chinook"}], empty=[], errors=[])

    engine.add_demo = add_demo
    response = client.post(f"{API}/jobs/demo", json={})
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "demo"

    job = wait_for(client, response.json()["id"])

    assert job["status"] == "succeeded"
    assert job["result"] == {"ok": True, "stats": [{"datasource_id": "chinook"}], "empty": [], "failed": []}
    assert [p["step"] for p in job["progress"]] == ["chinook", "support", "web_analytics"]
    assert [p["total"] for p in job["progress"]] == [3, 3, 3]
    assert seen["user"] == ADMIN  # the caller reaches add_demo, which checks the admin role itself


def test_demo_job_is_admin(api_client):
    client, engine = api_client(authenticated=False, api={"auth": "static", "static_role": "analyst"})
    engine.add_demo = lambda **kwargs: pytest.fail("a non-admin reached add_demo")

    assert client.post(f"{API}/jobs/demo", json={}).status_code == 403


def test_demo_job_takes_no_body_fields(stub):
    client, _ = stub

    assert client.post(f"{API}/jobs/demo", json={"datasource_ids": ["x"]}).status_code == 422


def test_demo_job_that_finds_the_config_changed_fails_with_its_code(stub):
    from nl2sql.review.models import ReviewError, ReviewErrorCode

    client, engine = stub

    def add_demo(**kwargs):
        raise ReviewError(ReviewErrorCode.FILE_CHANGED, "nl2sql.yaml changed on disk since the engine loaded it.")

    engine.add_demo = add_demo
    job = wait_for(client, client.post(f"{API}/jobs/demo", json={}).json()["id"])

    assert job["status"] == "failed" and job["error"]["code"] == "FILE_CHANGED"
