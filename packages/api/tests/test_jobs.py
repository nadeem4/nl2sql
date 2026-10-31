"""Background jobs: one worker, in memory, owned by the caller (docs/architecture/wave_d.md, D206 and "Jobs")."""
import asyncio
import json
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi import Request

from nl2sql import NL2SQLError, UserContext
from nl2sql_api import identity
from nl2sql_api.errors import RequestRefused
from nl2sql_api.jobs import DashboardJob, IndexJob, IndexResult, Jobs
from nl2sql_api.main import app, create_app

ANN = UserContext(user_id="ann", tenant_id="t", roles=["analyst"])
BOB = UserContext(user_id="bob", tenant_id="t", roles=["analyst"])
ROOT = UserContext(user_id="root", tenant_id="t", roles=["admin"])


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


@pytest.fixture
def jobs():
    manager = Jobs(poll_s=0.01)
    yield manager
    manager.close()


def _index(jobs, user, run, **params):
    return jobs.submit("index", IndexJob(**params), user, run)


def test_jobs_run_one_at_a_time_in_order(jobs):
    running, order, release = [], [], threading.Event()

    def runner(name):
        def run(ctx):
            running.append(name)
            assert len(running) == 1, "two jobs ran at once"
            order.append(name)
            release.wait(5)
            running.remove(name)
            return IndexResult(ok=True)
        return run

    users = (ANN, BOB, ROOT)
    ids = [_index(jobs, user, runner(user.user_id)).id for user in users]
    wait_for(lambda: order == ["ann"])
    assert [jobs.get(i, user).status for i, user in zip(ids, users)] == ["running", "queued", "queued"]
    release.set()
    wait_for(lambda: jobs.get(ids[2], ROOT).status == "succeeded")
    assert order == ["ann", "bob", "root"]


def test_api_runs_one_worker(jobs):
    release = threading.Event()
    for user in (ANN, BOB, ROOT):
        _index(jobs, user, lambda ctx: release.wait(5))
    wait_for(lambda: any(t.name == "nl2sql-jobs" for t in threading.enumerate()))
    mine = [t for t in threading.enumerate() if t.name == "nl2sql-jobs" and t is jobs._worker]
    assert len(mine) == 1
    release.set()


def test_serve_has_no_workers_option():
    from nl2sql_api import serve

    with pytest.raises(SystemExit):
        serve.main(["serve", "--workers", "2"])


def _frames(text):
    return [(block.split("\n")[0].removeprefix("event: "), json.loads(block.split("\n")[1].removeprefix("data: ")))
            for block in text.split("\n\n") if block.startswith("event: ")]


def test_job_progress_replays_then_follows(jobs):
    first, second = threading.Event(), threading.Event()

    def run(ctx):
        ctx.progress("chinook", 1, 2)
        first.set()
        second.wait(5)
        ctx.progress("sales", 2, 2)
        return IndexResult(ok=True, stats=[{"datasource_id": "chinook"}])

    job = _index(jobs, ANN, run)
    assert first.wait(5)

    async def follow():
        chunks = []
        async for chunk in jobs.stream(job.id):
            chunks.append(chunk)
            if len(chunks) == 1:  # the replay of what happened before we connected
                second.set()
        return "".join(chunks)

    frames = _frames(asyncio.run(follow()))
    assert [name for name, _ in frames] == ["job.progress", "job.progress", "job.finished"]
    assert [data["step"] for name, data in frames[:2]] == ["chinook", "sales"]
    assert frames[2][1]["status"] == "succeeded" and frames[2][1]["result"]["ok"] is True


def test_job_events_hold_no_worker_thread(jobs):
    release = threading.Event()
    job = _index(jobs, ANN, lambda ctx: release.wait(5))
    wait_for(lambda: jobs.get(job.id, ANN).status == "running")

    async def wait_quietly():
        stream = jobs.stream(job.id)
        assert hasattr(stream, "__anext__"), "the stream must be an async generator"
        before = set(threading.enumerate())
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.1)  # several polls with nothing to send
        assert set(threading.enumerate()) <= before, "a waiting stream started a thread"
        pending.cancel()
        release.set()

    asyncio.run(wait_quietly())


def test_cancel_running_job_stops_between_items(api_client):
    client, _ = api_client()
    started, processed = threading.Event(), []

    def run(ctx):
        for i in range(1, 1000):
            if ctx.token.is_cancelled():
                break
            processed.append(i)
            ctx.progress(f"item {i}", i, 1000)
            started.set()
            time.sleep(0.01)

    job = app.state.jobs.submit("index", IndexJob(), UserContext(roles=["admin"]), run)
    assert started.wait(5)
    response = client.delete(f"/api/v1/jobs/{job.id}")
    assert response.status_code == 200
    wait_for(lambda: client.get(f"/api/v1/jobs/{job.id}").json()["status"] == "cancelled")
    assert len(processed) < 1000


def test_cancel_queued_job_removes_it(jobs):
    release, ran = threading.Event(), []
    first = _index(jobs, ANN, lambda ctx: release.wait(5))
    queued = _index(jobs, BOB, lambda ctx: ran.append("bob"))
    assert jobs.cancel(queued.id, BOB).status == "cancelled"
    release.set()
    wait_for(lambda: jobs.get(first.id, ANN).status == "succeeded")
    time.sleep(0.05)
    assert ran == [] and jobs.get(queued.id, BOB).status == "cancelled"


def test_second_job_for_a_caller_is_a_conflict(jobs):
    release = threading.Event()
    first = _index(jobs, ANN, lambda ctx: release.wait(5))
    with pytest.raises(RequestRefused) as refused:
        _index(jobs, ANN, lambda ctx: None)
    assert refused.value.status == 409 and first.id in refused.value.message
    release.set()
    wait_for(lambda: jobs.get(first.id, ANN).status == "succeeded")
    assert _index(jobs, ANN, lambda ctx: None).id != first.id  # free again


def test_two_users_generate_for_one_datasource(jobs):
    release = threading.Event()
    params = DashboardJob(datasource_id="chinook")
    a = jobs.submit("dashboard", params, ANN, lambda ctx: release.wait(5))
    b = jobs.submit("dashboard", params, BOB, lambda ctx: release.wait(5))
    assert a.id != b.id
    release.set()


def test_a_failed_job_carries_an_error_and_no_exception_text(jobs):
    def run(ctx):
        raise RuntimeError("postgresql://admin:hunter2@db/sales")

    job = _index(jobs, ANN, run)
    wait_for(lambda: jobs.get(job.id, ANN).status == "failed")
    error = jobs.get(job.id, ANN).error
    assert error.code == "INTERNAL" and "hunter2" not in error.message


def test_a_failed_job_never_carries_a_server_error_message(jobs):
    def run(ctx):
        raise NL2SQLError("could not connect to postgresql://admin:hunter2@db/sales")

    job = _index(jobs, ANN, run)
    wait_for(lambda: jobs.get(job.id, ANN).status == "failed")
    error = jobs.get(job.id, ANN).error
    assert error.code == "INTERNAL" and "hunter2" not in error.message and "hunter2" not in error.fix


def test_a_refusal_keeps_its_message_and_details(jobs):
    def run(ctx):
        raise RequestRefused(409, "CONFLICT", "Already there.", details=[{"loc": ["x"], "msg": "dup"}])

    job = _index(jobs, ANN, run)
    wait_for(lambda: jobs.get(job.id, ANN).status == "failed")
    error = jobs.get(job.id, ANN).error
    assert error.code == "CONFLICT" and error.message == "Already there." and error.details == [{"loc": ["x"], "msg": "dup"}]


def test_a_cancel_after_the_runner_returned_keeps_the_job_succeeded():
    returned, holder = threading.Event(), {}

    def clock():
        if returned.is_set() and "cancelled" not in holder:
            holder["cancelled"] = holder["jobs"].cancel(holder["id"], ANN)  # arrives after the runner finished
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    manager = Jobs(poll_s=0.01, clock=clock)
    holder["jobs"] = manager
    try:
        def run(ctx):
            returned.set()
            return IndexResult(ok=True)

        holder["id"] = job_id = _index(manager, ANN, run).id
        wait_for(lambda: manager.get(job_id, ANN).status in ("succeeded", "cancelled", "failed"))
        done = manager.get(job_id, ANN)
        assert done.status == "succeeded" and done.result.ok is True
    finally:
        manager.close()


def test_only_the_last_jobs_are_kept():
    jobs = Jobs(keep=3, poll_s=0.01)
    try:
        ids = []
        for _ in range(5):
            job = _index(jobs, ANN, lambda ctx: None)
            wait_for(lambda: jobs.get(job.id, ANN).status == "succeeded")
            ids.append(job.id)
        assert [j.id for j in jobs.list(ANN)] == ids[:1:-1]
    finally:
        jobs.close()


def _as(users):
    def who(request: Request):
        return users[request.headers["x-user"]]
    return who


def test_another_users_job_is_not_found(api_client):
    client, _ = api_client()
    app.dependency_overrides[identity.caller] = _as({"ann": ANN, "bob": BOB, "root": ROOT})
    app.dependency_overrides[identity.admin] = _as({"ann": ANN, "bob": BOB, "root": ROOT})
    job = app.state.jobs.submit("index", IndexJob(), ANN, lambda ctx: IndexResult(ok=True))
    wait_for(lambda: app.state.jobs.get(job.id, ANN).status == "succeeded")
    url = f"/api/v1/jobs/{job.id}"
    for who, status in (("ann", 200), ("bob", 404), ("root", 200)):
        headers = {"x-user": who}
        assert client.get(url, headers=headers).status_code == status
        assert client.get(url + "/events", headers=headers).status_code == status
    assert client.delete(url, headers={"x-user": "bob"}).status_code == 404
    assert client.get(url, headers={"x-user": "bob"}).json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/api/v1/jobs", headers={"x-user": "bob"}).json() == []
    assert [j["id"] for j in client.get("/api/v1/jobs", headers={"x-user": "root"}).json()] == [job.id]


def test_job_routes_list_get_and_stream(api_client):
    client, _ = api_client()
    job = app.state.jobs.submit("index", IndexJob(datasource_ids=["chinook"]), UserContext(roles=["admin"]),
                                lambda ctx: (ctx.progress("chinook", 1, 1), IndexResult(ok=True))[1])
    wait_for(lambda: client.get(f"/api/v1/jobs/{job.id}").json()["status"] == "succeeded")
    body = client.get(f"/api/v1/jobs/{job.id}").json()
    assert body["kind"] == "index" and body["params"] == {"datasource_ids": ["chinook"]}
    assert body["progress"][0]["step"] == "chinook" and body["result"]["ok"] is True
    stream = client.get(f"/api/v1/jobs/{job.id}/events")
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert [name for name, _ in _frames(stream.text)] == ["job.progress", "job.finished"]
    assert client.get("/api/v1/jobs").json()[0]["id"] == job.id
    assert client.get("/api/v1/jobs/nope").status_code == 404


def test_job_routes_are_in_the_openapi_contract():
    paths = create_app().openapi()["paths"]
    assert set(paths["/api/v1/jobs/{job_id}"]) == {"get", "delete"}
    assert "get" in paths["/api/v1/jobs"] and "get" in paths["/api/v1/jobs/{job_id}/events"]
