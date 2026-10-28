"""MCP over HTTP: bearer keys, one identity per request, Host and Origin checks (docs/architecture/wave_c.md,
section 9, row M2).

The engine is real (no datasources); an ``ask`` is a run through ``QueryAPI.run_query`` replaced by a function that
answers with the caller it ran as, so no database and no model are involved (the fake LLM has nothing to answer:
``conversations.ask_only`` makes no model call). The requests go through Starlette's in-process client.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import anyio
import pytest

pytest.importorskip("mcp")
from starlette.testclient import TestClient  # noqa: E402

from nl2sql import NL2SQL, QueryResult, UserContext  # noqa: E402
from nl2sql.api.query_api import QueryAPI  # noqa: E402
from nl2sql.mcp import http, server  # noqa: E402
from nl2sql.testing.config import make_config  # noqa: E402

ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "analyst": {"description": "Reads", "allow": ["*"]},
         "viewer": {"description": "Reads", "allow": ["*"]}}
ADMIN = UserContext(user_id="root", roles=["admin"])
BASE = "http://127.0.0.1:8765"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class Runs:
    """Stands in for ``QueryAPI.run_query``: records each caller and answers with who it ran as."""

    def __init__(self, monkeypatch) -> None:
        self.callers: List[UserContext] = []
        runs = self

        def run_query(api: QueryAPI, question: str, **kwargs: Any) -> QueryResult:
            user = kwargs["user_context"]
            runs.callers.append(user)
            summary = f"{user.user_id}/{user.tenant_id}/{','.join(user.roles)}"
            answer = QueryResult(status="success", trace_id=kwargs["scope"].trace_id,
                                 final_answer={"summary": summary, "content": question, "format_type": "text"})
            api.close_scope(kwargs["scope"], answer)
            return answer

        monkeypatch.setattr(QueryAPI, "run_query", run_query)


@pytest.fixture
def runs(monkeypatch):
    return Runs(monkeypatch)


@pytest.fixture
def make(tmp_path, runs):
    engines: List[NL2SQL] = []

    def build(**sections: Any) -> NL2SQL:
        engine = NL2SQL(make_config(tmp_path, policies={"roles": ROLES, "admin_roles": ["admin"]}, **sections))
        engines.append(engine)
        return engine

    yield build
    for engine in engines:
        engine.close()


@pytest.fixture
def engine(make):
    return make()


def _token(engine, user="ana", roles=("analyst",), tenant=None):
    key, token = engine.api_keys.create(user_id=user, roles=list(roles), by=ADMIN, tenant_id=tenant)
    return key, token


def _call(client, tool, arguments=None, token=None, headers=None):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": tool, "arguments": arguments or {}}}
    sent = {**HEADERS, **({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})}
    return client.post("/mcp", json=request, headers=sent)


def _result(response):
    assert response.status_code == 200, response.text
    return response.json()["result"]["structuredContent"]


def _client(engine, identity=None):
    return TestClient(http.build_app(engine, identity), base_url=BASE)


def test_http_ask_needs_a_bearer_key(engine, runs):
    denied = []
    engine.events.subscribe(denied.append, ["mcp.denied"])
    with _client(engine) as client:
        missing = _call(client, "list_datasources")
        wrong_scheme = _call(client, "list_datasources", headers={"Authorization": "Basic abc"})
        malformed = _call(client, "list_datasources", token="not-a-key")
        unknown = _call(client, "list_datasources", token="nl2sql_" + "0" * 8 + "_" + "A" * 43)

    for response in (missing, wrong_scheme, malformed, unknown):
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    assert [e.data["reason"] for e in denied] == ["missing", "malformed", "malformed", "unknown"]
    assert runs.callers == []


def test_a_good_key_is_served(engine):
    _, token = _token(engine)
    with _client(engine) as client:
        body = _result(_call(client, "list_datasources", token=token))

    assert body == {"datasources": []}


def test_revoked_key_is_401_and_audited(engine, caplog):
    key, token = _token(engine)
    engine.api_keys.revoke(key.id, by=ADMIN)
    denied = []
    engine.events.subscribe(denied.append, ["mcp.denied"])
    caplog.set_level(logging.DEBUG)

    with _client(engine) as client:
        response = _call(client, "ask", {"question": "q"}, token=token)

    secret = token.split("_", 2)[2]
    assert response.status_code == 401
    assert [e.data for e in denied] == [{"reason": "revoked", "key_id": key.id}]
    for text in (response.text, str(denied[0]), caplog.text):
        assert token not in text and secret not in text


def test_a_forged_token_for_a_revoked_key_is_not_audited_as_revoked(engine):
    key, token = _token(engine)
    engine.api_keys.revoke(key.id, by=ADMIN)
    denied = []
    engine.events.subscribe(denied.append, ["mcp.denied"])
    secret = token.split("_", 2)[2]
    forged = f"nl2sql_{key.id}_{('B' if secret[0] != 'B' else 'C') + secret[1:]}"

    with _client(engine) as client:
        assert _call(client, "list_datasources", token=forged).status_code == 401

    assert [e.data for e in denied] == [{"reason": "unknown", "key_id": None}]


def test_tools_run_as_the_keys_roles(engine, runs):
    _, token = _token(engine, user="ana", roles=("viewer",), tenant="acme")
    with _client(engine) as client:
        body = _result(_call(client, "ask", {"question": "How many?"}, token=token))

    assert runs.callers == [UserContext(user_id="ana", tenant_id="acme", roles=["viewer"])]
    assert body["answer"]["summary"] == "ana/acme/viewer"


def test_two_keys_concurrently_run_with_their_own_roles(engine, runs, monkeypatch):
    """Request "a" is held inside the tool until request "b" has resolved its identity, so a global identity
    would hand "a" the caller of "b"."""
    _, ana = _token(engine, user="ana", roles=("analyst",), tenant="acme")
    _, bob = _token(engine, user="bob", roles=("viewer",), tenant="globex")
    b_resolved = threading.Event()
    real = server._off_the_loop

    async def held(caller, tool, engine_, *args):
        if args[0] == "b":
            caller()  # the identity of "b" is resolved now
            b_resolved.set()
        else:
            assert await anyio.to_thread.run_sync(b_resolved.wait, 10), "request b never resolved its identity"
        return await real(caller, tool, engine_, *args)

    monkeypatch.setattr(server, "_off_the_loop", held)

    with _client(engine) as client, ThreadPoolExecutor(2) as pool:
        first = pool.submit(lambda: _result(_call(client, "ask", {"question": "a"}, token=ana)))
        second = pool.submit(lambda: _result(_call(client, "ask", {"question": "b"}, token=bob)))
        got_a, got_b = first.result(timeout=20), second.result(timeout=20)

    assert got_a["answer"]["summary"] == "ana/acme/analyst"
    assert got_b["answer"]["summary"] == "bob/globex/viewer"


def test_foreign_host_or_origin_is_refused(engine, runs):
    _, token = _token(engine)
    with _client(engine) as client:
        host = _call(client, "ask", {"question": "q"}, token=token, headers={"Host": "evil.example"})
        origin = _call(client, "ask", {"question": "q"}, token=token, headers={"Origin": "http://evil.example"})
        local = _call(client, "ask", {"question": "q"}, token=token, headers={"Host": "localhost:8765"})

    assert host.status_code in (400, 403, 421) and origin.status_code in (400, 403, 421)
    assert local.status_code == 200
    assert len(runs.callers) == 1, "only the request with a good Host ran"


def test_allowed_origins_and_hosts_come_from_the_config(make):
    engine = make(mcp={"http": {"allowed_hosts": ["mcp.example.com"], "allowed_origins": ["https://chat.example.com"]}})
    _, token = _token(engine)
    with TestClient(http.build_app(engine, None), base_url="https://mcp.example.com") as client:
        allowed = _call(client, "list_datasources", token=token, headers={"Origin": "https://chat.example.com"})
        other = _call(client, "list_datasources", token=token, headers={"Origin": "https://other.example.com"})
        local = _call(client, "list_datasources", token=token, headers={"Host": "127.0.0.1:8765"})

    assert allowed.status_code == 200
    assert other.status_code in (400, 403, 421) and local.status_code in (400, 403, 421)


def test_the_path_comes_from_the_config(make):
    engine = make(mcp={"http": {"path": "/chat/mcp"}})
    _, token = _token(engine)
    with _client(engine) as client:
        moved = client.post("/chat/mcp", headers={**HEADERS, "Authorization": f"Bearer {token}"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_datasources", "arguments": {}}})
        old = _call(client, "list_datasources", token=token)

    assert moved.status_code == 200 and old.status_code == 404


def test_ask_returns_a_conversation_id_and_continues_it(engine):
    _, ana = _token(engine, user="ana")
    _, bob = _token(engine, user="bob")
    with _client(engine) as client:
        first = _result(_call(client, "ask", {"question": "How many customers?"}, token=ana))
        second = _result(_call(client, "ask", {"question": "And in Paris?", "conversation_id": first["conversation_id"]},
                               token=ana))
        stranger = _result(_call(client, "ask", {"question": "x", "conversation_id": first["conversation_id"]},
                                 token=bob))

    assert (first["turn"], first["question"]) == (1, "How many customers?")
    assert (second["turn"], second["question"], second["conversation_id"]) == (2, "And in Paris?",
                                                                               first["conversation_id"])
    turns = engine.conversations.get(first["conversation_id"], user_context=UserContext(user_id="ana",
                                                                                         roles=["analyst"])).turns
    assert [t.text for t in turns] == ["How many customers?", "And in Paris?"]
    assert stranger["errors"][0]["code"] == "CONVERSATION_NOT_FOUND"


def test_auth_none_serves_the_given_identity_on_a_loopback_host(make, runs):
    engine = make(mcp={"auth": "none"})
    me = UserContext(user_id="me", roles=["analyst"])
    with _client(engine, me) as client:
        body = _result(_call(client, "ask", {"question": "q"}))

    assert body["answer"]["summary"] == "me/None/analyst"


def test_auth_none_needs_an_identity(make):
    with pytest.raises(ValueError, match="identity"):
        http.build_app(make(mcp={"auth": "none"}), None)


def test_public_bind_without_keys_refuses_to_start(make, monkeypatch):
    started = []
    monkeypatch.setattr(http, "_run", lambda app, host, port: started.append((host, port)))
    engine = make(mcp={"auth": "none"})
    me = UserContext(user_id="me", roles=["analyst"])

    for host in ("0.0.0.0", "192.168.1.5", "example.com", "::"):
        with pytest.raises(ValueError, match="auth: api_keys"):
            http.serve(engine, me, host=host, port=8765)
    assert started == []

    for host in ("127.0.0.1", "localhost", "::1"):
        http.serve(engine, me, host=host, port=8765)
    assert [h for h, _ in started] == ["127.0.0.1", "localhost", "::1"]


def test_public_bind_with_keys_starts(engine, monkeypatch):
    started = []
    monkeypatch.setattr(http, "_run", lambda app, host, port: started.append((host, port)))

    http.serve(engine, None, host="0.0.0.0", port=9000)

    assert started == [("0.0.0.0", 9000)]


def test_serve_defaults_to_the_configured_host_and_port(make, monkeypatch):
    started = []
    monkeypatch.setattr(http, "_run", lambda app, host, port: started.append((host, port)))

    http.serve(make(mcp={"http": {"host": "127.0.0.1", "port": 9111}}), None)

    assert started == [("127.0.0.1", 9111)]


def test_a_failing_key_check_is_a_500_without_the_token(engine, monkeypatch, caplog):
    _, token = _token(engine)
    monkeypatch.setattr(type(engine.api_keys), "check", lambda self, t: (_ for _ in ()).throw(RuntimeError("down")))
    caplog.set_level(logging.DEBUG)

    with _client(engine) as client:
        response = _call(client, "list_datasources", token=token)

    assert response.status_code == 500 and token not in response.text + caplog.text


def test_the_request_log_never_holds_the_token(engine, caplog):
    _, token = _token(engine)
    caplog.set_level(logging.DEBUG)
    with _client(engine) as client:
        _call(client, "ask", {"question": "q"}, token=token)
        _call(client, "ask", {"question": "q"}, token="nl2sql_" + "0" * 8 + "_" + "A" * 43)

    assert token.split("_", 2)[2] not in caplog.text


def test_every_path_and_method_needs_a_key(engine, runs):
    denied = []
    engine.events.subscribe(denied.append, ["mcp.denied"])
    with _client(engine) as client:
        responses = [client.get("/other"), client.options("/mcp"), client.get("/mcp"), client.post("/other", json={})]

    assert [r.status_code for r in responses] == [401, 401, 401, 401]
    assert [e.data["reason"] for e in denied] == ["missing"] * 4
    assert runs.callers == []


def test_only_the_lifespan_passes_without_a_key(engine):
    app = http.build_app(engine, None)
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    anyio.run(app, {"type": "websocket", "path": "/mcp", "headers": []}, receive, send)

    assert [m["type"] for m in sent] == ["websocket.close"]


def test_auth_none_refuses_a_public_allowed_host(make):
    me = UserContext(user_id="me", roles=["analyst"])

    for hosts in (["mcp.example.com"], ["127.0.0.1:*", "chat.example.com:*"], ["10.0.0.5"]):
        engine = make(mcp={"auth": "none", "http": {"allowed_hosts": hosts}})
        with pytest.raises(ValueError, match="allowed_hosts"):
            http.build_app(engine, me)

    ok = make(mcp={"auth": "none", "http": {"allowed_hosts": ["localhost:*", "127.0.0.1", "[::1]:*"]}})
    http.build_app(ok, me)


def test_a_public_allowed_host_is_fine_with_keys(make):
    http.build_app(make(mcp={"http": {"allowed_hosts": ["mcp.example.com"]}}), None)


def test_the_default_hosts_cover_every_loopback_form(engine):
    _, token = _token(engine)
    with _client(engine) as client:
        statuses = [_call(client, "list_datasources", token=token, headers={"Host": host}).status_code
                    for host in ("[::1]:8765", "localhost", "127.0.0.1", "localhost:9", "127.0.0.1:9")]

    assert statuses == [200] * 5
