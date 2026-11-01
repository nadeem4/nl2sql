"""``/api/v1/conversations`` and ``/chat-tools``: the chat routes over a real engine (docs/architecture/wave_d.md, A2a).

The engine is the real facade. Its conversation model is a ``FakeLLMServer`` answering the ``ModelStep`` schema from a
script the test fills; an ``ask`` step is ``QueryAPI.run_query`` replaced by a function returning canned
``QueryResult``s, so no database, key or network is needed. Callers ``ann`` and ``bob`` are set per request.
"""
import asyncio
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, ClassVar, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from nl2sql import NL2SQL, Event, Registry, UserContext
from nl2sql.api.query_api import QueryAPI, QueryResult, RowSample, SubQueryResult
from nl2sql.common.errors import ApprovalNotFound
from nl2sql.chat_tools import ToolContext, ToolOutput
from nl2sql.clarify.models import ClarificationOption, ClarificationRequest
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.explain.models import ExplainedStep
from nl2sql.hooks.approval import ApprovalRequest
from nl2sql.testing.config import make_config
from nl2sql.testing.fake_llm import FakeLLMServer, Rule
from nl2sql_api import identity, sse
from nl2sql_api.dependencies import get_engine
from nl2sql_api.main import app

ANN = UserContext(user_id="ann", tenant_id="acme", roles=["analyst"])
BOB = UserContext(user_id="bob", tenant_id="acme", roles=["analyst"])
URL = "/api/v1/conversations"


# ------------------------------------------------------------------ the fake model, the canned runs, the engine

class Model:
    def __init__(self) -> None:
        self.script: List[Any] = []

    def say(self, *steps: Any) -> None:
        self.script.extend(steps)

    def answer(self, prompt: str) -> Dict[str, Any]:
        return self.script.pop(0) if self.script else {"reply": "Done."}


def ask(question: str) -> Dict[str, Any]:
    return {"call": {"name": "ask", "args": {"question": question}}}


def reply(text: str) -> Dict[str, Any]:
    return {"reply": text}


def result(question: str = "q", *, summary: str = "There are many.", status: str = "success", **fields: Any):
    return QueryResult(
        sub_queries=[SubQueryResult(id="sq1", datasource_id="ds1", status="ok", sql="SELECT 1", plan={},
                                    rows=RowSample(columns=["n"], rows=[[59]], total_rows=1))],
        final_answer={"summary": summary, "format_type": "text", "content": summary, "warnings": []},
        result_manifest=[ResultSummary(node_id="sq1", columns=["n"], row_count=1)], status=status, **fields)


def clarification() -> ClarificationRequest:
    step = ExplainedStep(sub_query_id="sq1", datasource_id="ds1", source="generated", text="t")
    return ClarificationRequest.build(
        question="What were sales last year?", asker_user_id="ann", asker_tenant_id="acme", phrase="sales",
        datasource_id="ds1", schema_version="v1", knowledge_digest="k", sub_query={"id": "sq1"},
        options=[ClarificationOption(id="a", label="Gross sales", plan={}, explanation=step),
                 ClarificationOption(id="b", label="Invoiced amount", plan={}, explanation=step)])


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class Echo:
    interface_version: ClassVar[int] = 1
    description: ClassVar[str] = "Echo some text back."
    args_model: ClassVar[type[BaseModel]] = EchoArgs

    def __call__(self, ctx: ToolContext, args: BaseModel) -> ToolOutput:
        return ToolOutput(kind="markdown", title="Echo", body=args.text)


class World:
    """The engine, the model's script, the runs, and a client that is ``who`` when it asks."""

    def __init__(self, engine: NL2SQL, model: Model, client: TestClient) -> None:
        self.engine, self.model, self.client = engine, model, client
        self.who = ANN
        self.respond: Callable[[str, Dict[str, Any]], QueryResult] = lambda question, kwargs: result(question)
        self.calls: List[Dict[str, Any]] = []
        self.gate: Optional[Callable[[], None]] = None

    def start(self, **body: Any) -> str:
        response = self.client.post(URL, json=body)
        assert response.status_code == 201, response.text
        return response.json()["id"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_ROUTES_FAKE_KEY", "sk-fake")
    model = Model()
    server = FakeLLMServer([Rule("ModelStep", model.answer)]).start()
    registry = Registry.default()
    registry.add_chat_tool("echo", Echo)
    config = make_config(tmp_path / "project", chat_tools=["echo", "report"],
                         llm={"provider": "openai", "model": "gpt-chat-routes", "api_key": "${env:CHAT_ROUTES_FAKE_KEY}",
                              "base_url": server.base_url})
    engine = NL2SQL(config, registry=registry)
    client = TestClient(app, raise_server_exceptions=False)
    box = World(engine, model, client)

    def run_query(api: QueryAPI, question: str, **kwargs: Any) -> QueryResult:
        box.calls.append({"question": question, **kwargs})
        if box.gate:
            box.gate()
        scope = kwargs["scope"]
        answer = box.respond(question, kwargs).model_copy(update={"trace_id": scope.trace_id})
        api.close_scope(scope, answer)
        return answer

    monkeypatch.setattr(QueryAPI, "run_query", run_query)
    monkeypatch.setattr(sse, "HEARTBEAT_S", 0.05)
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[identity.caller] = lambda: box.who
    app.state.engine = engine
    yield box
    app.dependency_overrides.clear()
    del app.state.engine
    client.close()
    engine.close()
    server.stop()


def events(text: str) -> List[Dict[str, Any]]:
    """The ``event:`` and ``data:`` frames of an SSE body."""
    frames = []
    for block in text.split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]
        if lines:
            fields = dict(line.split(": ", 1) for line in lines)
            frames.append({"event": fields["event"], "data": json.loads(fields["data"])})
    return frames


# ------------------------------------------------------------------ start, ask, follow up

def test_start_ask_follow_up_round_trip(world):
    world.model.say(ask("How many customers are there?"), reply("There are 59."), reply("Ten of them are in Brazil."))
    cid = world.start(datasource_id="ds1")

    first = world.client.post(f"{URL}/{cid}/turns", json={"text": "How many customers?"})
    second = world.client.post(f"{URL}/{cid}/turns", json={"text": "And how many in Brazil?"})

    assert first.status_code == 200, first.text
    assert (first.json()["index"], first.json()["status"], first.json()["reply"]) == (1, "answered", "There are 59.")
    assert first.json()["steps"][0]["call"]["name"] == "ask"
    assert second.json()["index"] == 2 and second.json()["reply"] == "Ten of them are in Brazil."
    stored = world.client.get(f"{URL}/{cid}").json()
    assert [t["text"] for t in stored["turns"]] == ["How many customers?", "And how many in Brazil?"]
    assert (stored["user_id"], stored["datasource_id"]) == ("ann", "ds1")
    assert world.calls[0]["user_context"].user_id == "ann", "the run is the caller's"
    listed = world.client.get(URL).json()
    assert [(i["id"], i["turns"]) for i in listed] == [(cid, 2)]
    assert world.client.delete(f"{URL}/{cid}").status_code == 204
    assert world.client.get(f"{URL}/{cid}").status_code == 404
    assert world.client.get(URL).json() == []


def test_start_for_a_saved_dashboard(world):
    draft = world.engine.dashboards.create_draft(user_context=ANN, title="Sales")

    started = world.client.post(URL, json={"dashboard_id": draft.id})
    missing = world.client.post(URL, json={"dashboard_id": "nope"})

    assert started.status_code == 201 and started.json()["dashboard_id"] == draft.id
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_a_turn_needs_text_or_a_choice_and_nothing_else(world):
    cid = world.start()

    for body in ({}, {"text": "hi", "choice": "a"}, {"text": "hi", "surprise": 1}):
        response = world.client.post(f"{URL}/{cid}/turns", json=body)
        assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_REQUEST", body
    blank = world.client.post(f"{URL}/{cid}/turns", json={"text": "   "})
    assert blank.status_code == 422 and blank.json()["error"]["code"] == "INVALID_ARGUMENT"
    nothing_pending = world.client.post(f"{URL}/{cid}/turns", json={"choice": "a"})
    assert nothing_pending.status_code == 422


def test_other_users_conversation_is_404(world):
    cid = world.start()
    world.who = BOB

    bodies = {"/turns": {"text": "x"}, "/turns/stream": {"text": "x"}, "/retry": {}, "/tools/echo": {}}
    for method, path in (("get", ""), ("delete", ""), ("post", "/turns"), ("post", "/turns/stream"),
                         ("post", "/retry"), ("post", "/tools/echo"), ("get", "/report")):
        response = getattr(world.client, method)(f"{URL}/{cid}{path}", **({"json": bodies[path]} if method == "post" else {}))
        assert response.status_code == 404, f"{method} {path}: {response.text}"
        assert response.json()["error"]["code"] == "NOT_FOUND"
    assert world.client.get(URL).json() == []


# ------------------------------------------------------------------ streaming

def test_turn_stream_ends_with_conversation_turn(world):
    world.model.say(ask("How many?"), reply("There are 59."))
    cid = world.start()

    response = world.client.post(f"{URL}/{cid}/turns/stream", json={"text": "How many?"})

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    frames = events(response.text)
    assert frames[-1]["event"] == "conversation.turn"
    assert frames[-1]["data"]["data"]["stored"]["reply"] == "There are 59."


def test_turn_stream_carries_conversation_step(world):
    world.model.say(ask("How many?"), reply("There are 59."))
    cid = world.start()

    frames = events(world.client.post(f"{URL}/{cid}/turns/stream", json={"text": "How many?"}).text)

    names = [f["event"] for f in frames]
    assert "conversation.step" in names and names.index("conversation.step") < names.index("conversation.turn")


def _asgi_post(path: str, body: dict, *, hang_up_after_first_chunk: threading.Event):
    """POST ``body`` to the app over raw ASGI (the test client waits for a whole response); disconnect as soon as the
    first body chunk is sent, then return what was sent."""

    async def go():
        payload = json.dumps(body).encode()
        inbox: asyncio.Queue = asyncio.Queue()
        await inbox.put({"type": "http.request", "body": payload, "more_body": False})
        sent: List[dict] = []

        async def receive():
            return await inbox.get()

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                hang_up_after_first_chunk.set()

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "scheme": "http", "server": ("localhost", 80),
                 "client": ("127.0.0.1", 1), "root_path": "", "app": app, "state": {},
                 "headers": [(b"host", b"localhost"), (b"content-type", b"application/json"),
                             (b"content-length", str(len(payload)).encode())]}
        task = asyncio.ensure_future(app(scope, receive, send))
        while not hang_up_after_first_chunk.is_set():
            await asyncio.sleep(0.01)
        await inbox.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 10)
        return sent

    return asyncio.run(go())


def test_closing_the_stream_cancels_the_turn(world):
    """The route hands the facade's iterator to the SSE helper, which closes it when the client goes away: that is
    what cancels a facade turn (``test_conversations.py`` shows the closed turn is not stored)."""
    closed, proceed, first_chunk = threading.Event(), threading.Event(), threading.Event()

    def stream(*args, **kwargs):
        try:
            yield Event(name="conversation.step", at=datetime.now(timezone.utc), data={"tool": "ask"})
            proceed.wait(10)  # a long step: nothing more to send
            yield Event(name="conversation.turn", at=datetime.now(timezone.utc), data={})
        finally:
            closed.set()

    world.engine.conversations = SimpleNamespace(stream=stream)

    early: List[bool] = []

    def end_the_step_later():
        first_chunk.wait(10)
        threading.Event().wait(0.3)  # the client has hung up; the step is still running
        early.append(closed.is_set())
        proceed.set()

    helper = threading.Thread(target=end_the_step_later)
    helper.start()

    sent = _asgi_post(f"{URL}/cid/turns/stream", {"text": "How many?"}, hang_up_after_first_chunk=first_chunk)
    helper.join(10)

    assert sent[0]["status"] == 200 and early == [False]
    assert closed.wait(10), "the disconnect closed the facade's iterator once its step returned"


def test_a_failure_after_the_settle_wait_is_a_final_run_failed_frame(world, monkeypatch):
    """The route has answered 200 by then, so the failure is the stream's last frame, not a cut-off body."""
    monkeypatch.setattr("nl2sql_api.routes.conversations.SETTLE_S", 0.05)

    def stream(*args, **kwargs):
        threading.Event().wait(0.3)  # past the wait: the 200 is already sent
        raise RuntimeError("secret host db.internal")
        yield  # pragma: no cover

    world.engine.conversations = SimpleNamespace(stream=stream)

    response = world.client.post(f"{URL}/cid/turns/stream", json={"text": "How many?"})

    frames = events(response.text)
    assert response.status_code == 200
    assert frames[-1]["event"] == "run.failed" and len(frames) == 1
    assert frames[-1]["data"]["data"]["error_code"] == "UNKNOWN_ERROR"
    assert "secret" not in response.text


def test_a_hung_up_turn_through_the_real_facade_stores_nothing(world):
    cid = world.start()
    world.model.say(ask("How many?"), reply("There are 59."))
    inside, proceed, first_chunk = threading.Event(), threading.Event(), threading.Event()
    world.gate = lambda: (inside.set(), proceed.wait(10))

    def release_later():
        inside.wait(10)
        first_chunk.wait(10)  # the client has hung up while the ask step is running
        threading.Event().wait(0.3)
        proceed.set()

    helper = threading.Thread(target=release_later)
    helper.start()
    _asgi_post(f"{URL}/{cid}/turns/stream", {"text": "How many?"}, hang_up_after_first_chunk=first_chunk)
    helper.join(10)
    for _ in range(50):  # the turn ends when the step in flight returns
        if world.calls:
            break
        threading.Event().wait(0.1)
    threading.Event().wait(0.5)

    assert len(world.calls) == 1, "the step that was running finished"
    assert world.client.get(f"{URL}/{cid}").json()["turns"] == [], "a turn nobody stayed for is not stored"


# ------------------------------------------------------------------ busy, clarification, approvals

def _hold_a_turn(world, cid):
    """Starts a turn that stays inside its ask step until ``release`` is set."""
    inside, release = threading.Event(), threading.Event()
    world.gate = lambda: (inside.set(), release.wait(10))
    world.model.say(ask("q"), reply("ok"))
    holder = TestClient(app, raise_server_exceptions=False)
    worker = threading.Thread(target=lambda: holder.post(f"{URL}/{cid}/turns", json={"text": "q"}))
    worker.start()
    assert inside.wait(10)
    return release, worker


def test_busy_conversation_is_409(world):
    cid = world.start()
    release, worker = _hold_a_turn(world, cid)
    try:
        again = world.client.post(f"{URL}/{cid}/turns", json={"text": "again"})
        streamed = world.client.post(f"{URL}/{cid}/turns/stream", json={"text": "again"})
        deleted = world.client.delete(f"{URL}/{cid}")
    finally:
        release.set()
        worker.join(10)

    for response in (again, streamed, deleted):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CONFLICT"
    assert world.client.post(f"{URL}/{cid}/turns", json={"text": "now"}).status_code == 200


def test_needs_clarification_then_choice_turn(world):
    request = clarification()
    world.respond = lambda question, kwargs: (
        result(question, status="needs_clarification", clarification=request) if kwargs.get("choice") is None
        else result(question, summary="Gross sales were 10."))
    world.model.say(ask(request.question))
    cid = world.start()

    first = world.client.post(f"{URL}/{cid}/turns", json={"text": request.question}).json()
    pending = world.client.get(f"{URL}/{cid}").json()["pending"]
    bad = world.client.post(f"{URL}/{cid}/turns", json={"choice": "zzz"})
    second = world.client.post(f"{URL}/{cid}/turns", json={"choice": "a"}).json()

    assert first["status"] == "needs_clarification"
    assert [o["id"] for o in pending["options"]] == ["a", "b"]
    assert bad.status_code == 422
    assert (second["kind"], second["text"], second["status"]) == ("choice", "Gross sales", "answered")
    assert world.client.get(f"{URL}/{cid}").json()["pending"] is None


def test_retry_with_approval_ids(world):
    need = ApprovalRequest.build(question="Show customers", asker=ANN, hook="review", datasource_id="ds1",
                                 sql="SELECT 1", reason="Reads customers", approver_roles=["admin"])
    world.respond = lambda question, kwargs: result(question, status="needs_approval", approvals_needed=[need])
    world.model.say(ask("Show customers"))
    cid = world.start()
    first = world.client.post(f"{URL}/{cid}/turns", json={"text": "Show customers"}).json()
    world.respond = lambda question, kwargs: result(question, summary="Here they are.")

    retried = world.client.post(f"{URL}/{cid}/retry", json={"approval_ids": [need.id]})

    assert first["status"] == "needs_approval"
    assert retried.status_code == 200 and retried.json()["kind"] == "retry" and retried.json()["status"] == "answered"
    assert world.calls[-1]["approvals"] == [need.id], "request ids go to the facade as they are"
    nothing = world.client.post(f"{URL}/{world.start()}/retry", json={})
    assert nothing.status_code == 422, "no question to run again"


def test_retry_with_an_unknown_approval_id_is_404(world):
    world.model.say(ask("Show customers"))
    cid = world.start()
    world.client.post(f"{URL}/{cid}/turns", json={"text": "Show customers"})

    def unknown(question, kwargs):  # what the engine raises for an id that is unknown, waiting, denied or expired
        raise ApprovalNotFound("No approval request no-such-request.")

    world.respond = unknown

    response = world.client.post(f"{URL}/{cid}/retry", json={"approval_ids": ["no-such-request"]})

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------------ tools and the report

def test_tool_route_returns_tool_output(world):
    cid = world.start()

    turn = world.client.post(f"{URL}/{cid}/tools/echo", json={"args": {"text": "hello"}})
    bad_args = world.client.post(f"{URL}/{cid}/tools/echo", json={"args": {"txt": "x"}})
    unknown = world.client.post(f"{URL}/{cid}/tools/nope", json={})

    assert turn.status_code == 200
    [step] = turn.json()["steps"]
    assert (turn.json()["kind"], step["call"]["name"], step["output"]["kind"], step["output"]["body"]) == (
        "tool", "echo", "markdown", "hello")
    assert bad_args.status_code == 422 and "not valid" in bad_args.json()["error"]["message"]
    assert unknown.status_code == 200 and unknown.json()["steps"][0]["error"]["code"] == "UNKNOWN_CHAT_TOOL"
    listed = world.client.get("/api/v1/chat-tools").json()
    assert [t["name"] for t in listed] == ["echo", "report"]
    assert listed[0]["description"] == "Echo some text back." and "properties" in listed[0]["args_schema"]


def test_report_is_markdown(world):
    world.model.say(ask("How many customers are there?"), reply("There are 59."))
    cid = world.start()
    world.client.post(f"{URL}/{cid}/turns", json={"text": "How many customers are there?"})

    response = world.client.get(f"{URL}/{cid}/report", params={"turns": [1], "title": "Customers"})
    empty = world.client.get(f"{URL}/{world.start()}/report")
    missing_turn = world.client.get(f"{URL}/{cid}/report", params={"turns": [7]})

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("# Customers")
    assert empty.status_code == 422 and missing_turn.status_code == 422
