"""The index routes and the index and profile jobs (docs/architecture/wave_d.md, row A3). A stub engine records the
calls and answers with the engine's own models."""
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from dashboard_stubs import Recorder

from nl2sql import DatasourceNotFound, DriftReport, IndexProgress, ProfileReport, UserContext

API = "/api/v1"
ADMIN = UserContext(roles=["admin"])
HEALTH = {
    "status": "stale",
    "total": 87,
    "counts": {"schema.table": 11, "schema.column": 64},
    "built_at": "2026-09-22T12:35:42+00:00",
    "embedding_model": "local/all-MiniLM-L6-v2",
    "namespace": "nl2sql__local-all-minilm-l6-v2__384",
    "datasources": [{"datasource_id": "chinook", "entries": 87, "index_version": "v1",
                     "built_at": "2026-09-22T12:35:42+00:00"}],
    "problems": ["Datasource 'chinook' was indexed from schema version v1, which is no longer in the state store."],
}
SECRET = "postgresql://u:hunter2@h/db"


@pytest.fixture
def stub(api_client):
    client, engine = api_client()
    engine.index, engine.knowledge = Recorder(), Recorder()
    return client, engine.index, engine.knowledge


def wait_for(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"{API}/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        assert time.monotonic() < deadline, "the job did not finish"
        time.sleep(0.01)


def built(**changes):
    return SimpleNamespace(**{"ok": True, "stats": [], "empty": [], "errors": [], "cancelled": False, **changes})


def test_index_is_the_engines_index_health(stub):
    client, index, _ = stub
    index.returns["health"] = HEALTH

    response = client.get(f"{API}/index")

    assert response.status_code == 200, response.text
    assert response.json() == HEALTH
    assert client.get(f"{API}/index/status").status_code == 404  # renamed, no alias


def test_clear_the_index(stub):
    client, index, _ = stub
    assert client.delete(f"{API}/index").json()["success"] is True
    assert index.called("clear") == [((), {})]


def test_the_old_index_routes_are_gone(stub):
    client, _, _ = stub
    assert client.post(f"{API}/index-all").status_code in (404, 405)
    assert client.post(f"{API}/index/chinook").status_code in (404, 405)


# ---- the index job

def test_index_job_progress_per_datasource(stub):
    client, index, _ = stub
    release = threading.Event()

    def build(datasource_ids, *, on_datasource, cancellation_token):
        on_datasource(IndexProgress(datasource_id="chinook", state="started"))
        on_datasource(IndexProgress(datasource_id="chinook", state="done", counts={"schema.table": 11}))
        release.wait(5)
        on_datasource(IndexProgress(datasource_id="support", state="failed", reason=SECRET))
        return built(ok=False, stats=[{"datasource_id": "chinook"}],
                     errors=[{"datasource_id": "support", "error": SECRET}])

    index.returns["build"] = build
    response = client.post(f"{API}/jobs/index", json={"datasource_ids": ["chinook", "support"]})
    assert response.status_code == 202
    job = response.json()
    assert job["kind"] == "index" and job["params"] == {"datasource_ids": ["chinook", "support"]}
    deadline = time.monotonic() + 5
    while not client.get(f"{API}/jobs/{job['id']}").json()["progress"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    release.set()
    done = wait_for(client, job["id"])
    assert done["status"] == "succeeded"
    assert [(p["step"], p["done"], p["total"]) for p in done["progress"]] == [("chinook", 1, 2),
                                                                             ("support (failed)", 2, 2)]
    assert done["result"] == {"ok": False, "stats": [{"datasource_id": "chinook"}], "empty": [],
                              "failed": ["support"]}
    assert "hunter2" not in str(done)  # the reason stays in the server log
    (args, kwargs), = index.called("build")
    assert args == (["chinook", "support"],) and kwargs["cancellation_token"] is not None


def test_index_job_progress_ends_at_its_total_when_an_id_repeats(stub):
    client, index, _ = stub

    def build(datasource_ids, *, on_datasource, cancellation_token):
        on_datasource(IndexProgress(datasource_id="chinook", state="done"))  # the build dedupes its ids
        return built()

    index.returns["build"] = build
    done = wait_for(client, client.post(f"{API}/jobs/index", json={"datasource_ids": ["chinook", "chinook"]}).json()["id"])
    assert [(p["done"], p["total"]) for p in done["progress"]] == [(1, 1)]


def test_index_job_of_every_datasource_passes_none(stub):
    client, index, _ = stub
    index.returns["build"] = lambda ids, **kwargs: built()

    wait_for(client, client.post(f"{API}/jobs/index", json={}).json()["id"])

    (args, _), = index.called("build")
    assert args == (None,)


def test_a_cancelled_index_build_ends_the_job_cancelled(stub):
    client, index, _ = stub
    started = threading.Event()

    def build(ids, *, on_datasource, cancellation_token):
        started.set()
        deadline = time.monotonic() + 5
        while not cancellation_token.is_cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        return built(ok=False, cancelled=True)

    index.returns["build"] = build
    job = client.post(f"{API}/jobs/index", json={}).json()
    assert started.wait(5)
    client.delete(f"{API}/jobs/{job['id']}")

    assert wait_for(client, job["id"])["status"] == "cancelled"


def test_index_job_body_is_strict(stub):
    client, _, _ = stub
    assert client.post(f"{API}/jobs/index", json={"datasource_ids": ["a"], "extra": 1}).status_code == 422


# ---- drift

def test_drift_check_route_is_admin(api_client):
    client, engine = api_client(authenticated=False, api={"auth": "static", "static_role": "analyst"})
    engine.index = Recorder()

    response = client.get(f"{API}/index/check")

    assert response.status_code == 403 and engine.index.calls == []


def test_drift_check_passes_the_ids_and_the_caller(stub):
    client, index, _ = stub
    report = DriftReport(datasource_id="chinook", status="unchanged", live_version="v1",
                         checked_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    index.returns["check"] = [report]

    params = [("datasource_id", "chinook"), ("datasource_id", "support")]
    body = client.get(f"{API}/index/check", params=params).json()

    assert body[0]["datasource_id"] == "chinook" and body[0]["status"] == "unchanged"
    assert index.called("check") == [((["chinook", "support"],), {"user_context": ADMIN})]
    client.get(f"{API}/index/check")
    assert index.called("check")[-1][0] == (None,)


def test_drift_check_of_an_unknown_datasource_is_a_404(stub):
    client, index, _ = stub
    index.returns["check"] = DatasourceNotFound("Unknown datasource ID: nope")
    assert client.get(f"{API}/index/check", params={"datasource_id": "nope"}).status_code == 404


# ---- the retrieval inspector

def test_inspect_route(stub):
    client, index, _ = stub
    index.returns["inspect_defaults"] = {"k": 8, "lambda_mult": 0.5, "fetch_multiplier": 4, "types": ["schema.table"]}
    index.returns["inspect"] = {"pool": [], "picks": [], "dropped": []}

    assert client.get(f"{API}/index/inspect").json()["k"] == 8
    response = client.post(f"{API}/index/inspect", json={"query": "revenue", "k": 3, "lambda_mult": 0.2,
                                                         "types": ["schema.table"], "datasource_id": "chinook"})

    assert response.status_code == 200 and response.json() == {"pool": [], "picks": [], "dropped": []}
    assert index.called("inspect") == [(("revenue",), {"k": 3, "lambda_mult": 0.2, "types": ["schema.table"],
                                                       "datasource_id": "chinook", "user_context": ADMIN})]


def test_inspect_defaults_when_the_body_leaves_them_out(stub):
    client, index, _ = stub
    index.returns["inspect"] = {"pool": [], "picks": [], "dropped": []}
    client.post(f"{API}/index/inspect", json={"query": "revenue"})
    assert index.called("inspect") == [(("revenue",), {"k": 8, "lambda_mult": None, "types": None,
                                                       "datasource_id": None, "user_context": ADMIN})]
    assert client.post(f"{API}/index/inspect", json={"query": "q", "k": 0}).status_code == 422
    assert client.post(f"{API}/index/inspect", json={"k": 3}).status_code == 422


# ---- the profile job

def test_profile_job_creates_proposals_on_the_server_key(stub):
    client, _, knowledge = stub

    def report(ds):
        return ProfileReport(datasource_id=ds, schema_version="v1", proposals={"join": 2}, seconds=0.5)

    def profile(datasource_ids, *, user_context, draft, max_cost_usd, on_datasource, cancellation_token):
        for ds in datasource_ids:
            on_datasource(report(ds))
        return [report(ds) for ds in datasource_ids]

    knowledge.returns["profile"] = profile
    response = client.post(f"{API}/jobs/profile", json={"datasource_ids": ["chinook", "support"], "draft": True,
                                                        "max_cost_usd": 0.5})
    assert response.status_code == 202 and response.json()["kind"] == "profile"
    done = wait_for(client, response.json()["id"])

    assert done["status"] == "succeeded"
    assert [(p["step"], p["done"], p["total"]) for p in done["progress"]] == [("chinook", 1, 2), ("support", 2, 2)]
    assert [r["datasource_id"] for r in done["result"]] == ["chinook", "support"]
    (args, kwargs), = knowledge.called("profile")
    assert args == (["chinook", "support"],)
    assert kwargs["user_context"] == ADMIN and kwargs["draft"] is True and kwargs["max_cost_usd"] == 0.5
    assert kwargs["cancellation_token"] is not None
    assert "llm_override" not in kwargs  # a key typed in the browser never pays for drafts


def test_profile_job_defaults_and_validation(stub):
    client, _, knowledge = stub
    knowledge.returns["profile"] = lambda ids, **kwargs: []
    wait_for(client, client.post(f"{API}/jobs/profile", json={}).json()["id"])
    (args, kwargs), = knowledge.called("profile")
    assert args == (None,) and kwargs["draft"] is False and kwargs["max_cost_usd"] is None
    assert client.post(f"{API}/jobs/profile", json={"max_cost_usd": 0}).status_code == 422
    assert client.post(f"{API}/jobs/profile", json={"nope": 1}).status_code == 422
    # a cost cap without drafts is a bad request now, not a job that fails later
    assert client.post(f"{API}/jobs/profile", json={"max_cost_usd": 1}).status_code == 422
    assert client.post(f"{API}/jobs/profile", json={"draft": True, "max_cost_usd": 1}).status_code == 202


def test_jobs_are_admin_only(api_client):
    client, engine = api_client(authenticated=False, api={"auth": "static", "static_role": "analyst"})
    engine.index, engine.knowledge = Recorder(), Recorder()

    assert client.post(f"{API}/jobs/index", json={}).status_code == 403
    assert client.post(f"{API}/jobs/profile", json={}).status_code == 403
    assert engine.index.calls == [] and engine.knowledge.calls == []
