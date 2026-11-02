"""The hosted demo: anonymous sessions, limits, one job and two streams per session (D221, "The hosted demo")."""
import asyncio
import json
import re
import threading
from types import SimpleNamespace

import pytest
from dashboard_stubs import Recorder
from fastapi import Depends
from fastapi.testclient import TestClient

from nl2sql import UserContext
from nl2sql.testing.config import make_config
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.hosted import SESSION_COOKIE, Hosted, HostedMiddleware, Limits
from nl2sql_api.jobs import IndexJob, Jobs
from nl2sql_api.main import create_app
from nl2sql_api.routes import web

POLICIES = {"roles": {"admin": {"description": "role", "allow": ["*"]}, "viewer": {"description": "role", "allow": ["*"]}}, "default_role": "viewer"}
SESSION = "a" * 32


def _engine(folder):
    engine = SimpleNamespace(config=make_config(folder, policies=POLICIES, mode="hosted",
                                                api={"auth": "static", "static_role": "viewer", "request_llm": True}))
    engine.datasources = Recorder()
    engine.datasources.returns["list"] = []
    engine.llm = Recorder()
    engine.llm.returns.update({"recorded": [], "recorded_with": None})
    return engine


@pytest.fixture
def make(tmp_path):
    made = []

    def _make(per_minute=100, per_session=100, clock=None):
        limits = Limits(per_minute, per_session, **({"clock": clock} if clock else {}))
        app = create_app(hosted=Hosted(role="viewer", limits=limits))
        engine = _engine(tmp_path / f"project-{len(made)}")
        app.dependency_overrides[get_engine] = lambda: engine
        app.state.engine, app.state.jobs = engine, Jobs(poll_s=0.01)
        made.append(app)
        return app, TestClient(app, raise_server_exceptions=False)

    yield _make
    for app in made:
        app.state.jobs.close()


def test_hosted_session_cookie_is_nl2sql_demo_session(make):
    _, client = make()
    cookie = client.get("/api/v1/health").headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=") and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    token = client.cookies[SESSION_COOKIE]
    assert re.fullmatch(r"[0-9a-f]{32}", token)  # 128 random bits
    assert "set-cookie" not in client.get("/api/v1/health").headers  # kept, not minted again


def test_a_cookie_that_is_not_ours_is_replaced(make):
    _, client = make()
    response = client.get("/api/v1/health", headers={"Cookie": f"{SESSION_COOKIE}=../../etc/passwd"})
    assert f"{SESSION_COOKIE}=" in response.headers["set-cookie"] and "passwd" not in response.headers["set-cookie"]


def test_a_visitor_is_an_anonymous_non_admin_with_the_default_role(make):
    app, client = make()
    seen = []

    @app.get("/api/v1/whoami")
    def whoami(user: UserContext = Depends(identity.caller)):
        seen.append(user)
        return user

    app.router.routes.insert(0, app.router.routes.pop())  # ahead of the page's catch-all
    client.get("/api/v1/whoami")
    TestClient(app).get("/api/v1/whoami")
    assert seen[0].roles == ["viewer"] and seen[0].user_id == Hosted.visitor_id(client.cookies[SESSION_COOKIE])
    assert seen[1].user_id != seen[0].user_id  # nothing a visitor makes is visible to another


def test_hosted_admin_routes_are_403(make):
    _, client = make()
    for method, path in (("GET", "/api/v1/api-keys"), ("POST", "/api/v1/jobs/index"), ("GET", "/api/v1/evals/suites"),
                         ("PUT", "/api/v1/settings/llm"), ("POST", "/api/v1/datasources")):
        response = client.request(method, path, json={})
        assert response.status_code == 403, (method, path, response.text)


def test_a_header_cannot_make_a_visitor_admin(make):
    _, client = make()
    response = client.get("/api/v1/api-keys", headers={"Authorization": "Bearer nl2sql_admin", "X-NL2SQL-Roles": "admin"})
    assert response.status_code == 403


def test_hosted_limits_are_429_with_retry_after(make):
    now = [0.0]
    _, client = make(per_minute=2, clock=lambda: now[0])
    codes = [client.post("/api/v1/query", json={}).status_code for _ in range(3)]
    assert 429 not in codes[:2] and codes[2] == 429
    blocked = client.post("/api/v1/query", json={})
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.json()["error"]["code"] == "LIMIT_REACHED" and "x-request-id" in blocked.headers
    now[0] += 60  # the bucket refills
    assert client.post("/api/v1/query", json={}).status_code != 429


def test_the_session_cap_is_per_session(make):
    _, client = make(per_session=2)
    assert 429 not in [client.post("/api/v1/query", json={}).status_code for _ in range(2)]
    capped = client.post("/api/v1/query", json={})
    assert capped.status_code == 429 and "2 questions" in capped.json()["error"]["message"]
    assert TestClient(client.app).post("/api/v1/query", json={}).status_code != 429  # another session


def test_reads_are_not_limited(make):
    _, client = make(per_minute=1, per_session=1)
    assert all(client.get("/api/v1/health").status_code == 200 for _ in range(5))


def test_the_meta_route_reports_the_hosted_limits_to_a_visitor(make):
    _, client = make(per_minute=7, per_session=11)
    body = client.get("/api/v1/meta").json()
    assert body["mode"] == "hosted"
    assert body["limits"] == {"questions_per_minute": 7, "questions_per_session": 11, "asked": 0,
                              "jobs": 1, "streams": 2}
    client.post("/api/v1/query", json={})
    assert client.get("/api/v1/meta").json()["limits"]["asked"] == 1


def test_a_visitors_id_is_a_one_way_hash_of_its_cookie():
    token, other = "a" * 32, "b" * 32

    visitor = Hosted.visitor_id(token)

    assert re.fullmatch(r"visitor-[0-9a-f]{32}", visitor) and token not in visitor
    assert visitor == Hosted.visitor_id(token) != Hosted.visitor_id(other)


def test_meta_names_a_hosted_visitor_by_its_derived_id(make):
    _, client = make()
    response = client.get("/api/v1/meta")
    token = client.cookies[SESSION_COOKIE]

    assert response.json()["caller"] == {"user_id": Hosted.visitor_id(token), "admin": False}
    assert token not in response.text


def _record(user_context, **_):
    from nl2sql import ApprovalRequest
    request = ApprovalRequest.build(question="Q?", asker=user_context, hook="h", datasource_id="d", sql="SELECT 1",
                                    reason="r", approver_roles=["boss"])
    from datetime import datetime, timezone
    from nl2sql.approvals.api import ApprovalRecord
    now = datetime.now(timezone.utc)
    return [ApprovalRecord(id=request.id, status="pending", request=request, created_at=now, updated_at=now,
                           own=True)]


def test_the_raw_cookie_is_in_no_body_the_engine_is_given_or_a_log_line(make, caplog):
    from datetime import datetime, timezone
    from nl2sql import Conversation
    app, client = make()
    engine = app.state.engine
    engine.approvals = Recorder()
    engine.approvals.returns["list"] = _record
    engine.conversations = Recorder()
    engine.conversations.returns["list"] = []
    engine.conversations.returns["get"] = lambda cid, user_context: Conversation(
        id=cid, user_id=user_context.user_id, title="t", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc))
    engine.dashboards = Recorder()
    engine.dashboards.returns["list"] = []
    client.get("/api/v1/health")
    token = client.cookies[SESSION_COOKIE]
    job = app.state.jobs.submit("index", IndexJob(), Hosted(role="viewer").user(Hosted.visitor_id(token)),
                                lambda ctx: None)

    with caplog.at_level("DEBUG"):
        bodies = [client.get(path) for path in (
            "/api/v1/meta", "/api/v1/approvals", "/api/v1/conversations", "/api/v1/conversations/c1",
            "/api/v1/dashboards", "/api/v1/jobs", f"/api/v1/jobs/{job.id}", "/api/v1/nowhere")]

    assert bodies[1].json()[0]["request"]["asker_user_id"] == Hosted.visitor_id(token)
    assert bodies[3].json()["user_id"] == Hosted.visitor_id(token)
    for response in bodies:
        assert token not in response.text
    for recorder in (engine.approvals, engine.conversations, engine.dashboards):
        assert token not in repr(recorder.calls)
    assert token not in caplog.text


def test_visitor_b_never_sees_visitor_as_cookie(make):
    app, a = make()
    a.get("/api/v1/health")
    token_a = a.cookies[SESSION_COOKIE]
    b = TestClient(app, raise_server_exceptions=False)
    body = b.get("/api/v1/meta")

    assert b.cookies[SESSION_COOKIE] != token_a
    assert token_a not in body.text and body.json()["caller"]["user_id"] != a.get("/api/v1/meta").json()["caller"]["user_id"]


def test_the_hosted_page_carries_the_preview_tags_and_starts_no_session(make, tmp_path, monkeypatch):
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")
    monkeypatch.setattr(web, "STATIC_DIR", tmp_path / "static")
    _, client = make()
    page = client.get("/", headers={"x-forwarded-proto": "https", "x-forwarded-host": "nl2sql.codewithnk.com"})
    assert 'property="og:image" content="https://nl2sql.codewithnk.com/social-card.png"' in page.text
    assert "set-cookie" not in page.headers


# ---- the job and stream caps, driven through the ASGI app so a stream can stay open

def _scope(method, path, app, session=SESSION):
    return {"type": "http", "method": method, "path": path, "app": app, "state": {},
            "headers": [(b"cookie", f"{SESSION_COOKIE}={session}".encode())]}


async def _call(middleware, scope):
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await middleware(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    return start["status"], headers, b"".join(m.get("body", b"") for m in sent)


def _wired(inner):
    hosted = Hosted(role="viewer", limits=Limits(1000, 1000))
    app = SimpleNamespace(state=SimpleNamespace(jobs=Jobs(poll_s=0.01), engine=None, hosted=hosted))
    return HostedMiddleware(inner, hosted=hosted), app, hosted


def test_hosted_one_job_and_two_streams_per_session():
    release = asyncio.Event()

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await release.wait()
        await send({"type": "http.response.body", "body": b"done"})

    async def run():
        middleware, app, _ = _wired(inner)
        stream = "/api/v1/query/stream"
        opened = [asyncio.create_task(_call(middleware, _scope("POST", stream, app))) for _ in range(2)]
        await asyncio.sleep(0.05)
        status, headers, body = await _call(middleware, _scope("POST", stream, app))  # the third
        assert status == 429 and json.loads(body)["error"]["code"] == "LIMIT_REACHED" and "retry-after" in headers
        other = asyncio.create_task(_call(middleware, _scope("GET", "/api/v1/jobs/abc/events", app, "b" * 32)))
        await asyncio.sleep(0.05)
        assert not other.done()  # another session has two of its own
        release.set()
        assert [r[0] for r in await asyncio.gather(*opened, other)] == [200, 200, 200]
        assert (await _call(middleware, _scope("POST", stream, app)))[0] == 200  # the slots are given back
        app.state.jobs.close()

    asyncio.run(run())


def test_hosted_one_job_per_session():
    release = threading.Event()

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def run():
        middleware, app, hosted = _wired(inner)
        app.state.jobs.submit("index", IndexJob(), hosted.user(Hosted.visitor_id(SESSION)), lambda ctx: release.wait(5))
        status, _, body = await _call(middleware, _scope("POST", "/api/v1/jobs/dashboard", app))
        assert status == 429 and json.loads(body)["error"]["code"] == "LIMIT_REACHED"
        assert (await _call(middleware, _scope("POST", "/api/v1/jobs/dashboard", app, "c" * 32)))[0] == 202
        assert (await _call(middleware, _scope("DELETE", "/api/v1/jobs/abc", app)))[0] == 202  # cancelling is free
        release.set()
        app.state.jobs.close()

    asyncio.run(run())


def test_hosted_parallel_job_starts_from_one_session_pass_one_at_a_time():
    release = asyncio.Event()

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await release.wait()  # the route has not submitted its job yet: the jobs list is still empty
        await send({"type": "http.response.body", "body": b"{}"})

    async def run():
        middleware, app, _ = _wired(inner)
        path = "/api/v1/jobs/dashboard"
        first = asyncio.create_task(_call(middleware, _scope("POST", path, app)))
        await asyncio.sleep(0.05)
        second = await _call(middleware, _scope("POST", path, app))
        assert second[0] == 429 and json.loads(second[2])["error"]["code"] == "LIMIT_REACHED"
        release.set()
        assert (await first)[0] == 202
        assert (await _call(middleware, _scope("POST", path, app)))[0] == 202  # the slot is given back
        app.state.jobs.close()

    asyncio.run(run())


def test_a_refused_job_start_gives_its_slot_back():
    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def run():
        hosted = Hosted(role="viewer", limits=Limits(1000, 1))
        app = SimpleNamespace(state=SimpleNamespace(jobs=Jobs(poll_s=0.01), engine=None, hosted=hosted))
        middleware = HostedMiddleware(inner, hosted=hosted)
        path = "/api/v1/jobs/dashboard"
        assert (await _call(middleware, _scope("POST", path, app)))[0] == 202
        assert (await _call(middleware, _scope("POST", path, app)))[0] == 429  # the session cap, not the slot
        assert hosted.job_starting(Hosted.visitor_id(SESSION)) is False
        app.state.jobs.close()

    asyncio.run(run())


@pytest.mark.parametrize("value", ["A" * 32, "a" * 31, "a" * 33, "g" * 32])
def test_only_the_cookie_this_server_mints_is_accepted(make, value):
    _, client = make()
    response = client.get("/api/v1/health", headers={"Cookie": f"{SESSION_COOKIE}={value}"})
    minted = re.search(rf"{SESSION_COOKIE}=([^;]+)", response.headers["set-cookie"]).group(1)
    assert re.fullmatch(r"[0-9a-f]{32}", minted) and minted != value


def test_the_session_cookie_is_secure_over_https_only(make):
    _, client = make()
    assert "secure" not in client.get("/api/v1/health").headers["set-cookie"].lower()
    over_https = TestClient(client.app, base_url="https://testserver").get("/api/v1/health")
    assert "secure" in over_https.headers["set-cookie"].lower()
    proxied = TestClient(client.app).get("/api/v1/health", headers={"x-forwarded-proto": "https"})
    assert "secure" in proxied.headers["set-cookie"].lower()
