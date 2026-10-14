"""``POST /api/ask/stream``: the playground's question, as Server-Sent Events.

The same rules as ``/api/ask`` -- the hosted key and limits, replay, the
replay-miss rewrite, feedback's record of the run -- with the run reported as
it happens. The ``final`` event's data is exactly what ``/api/ask`` returns.
"""
import json

import pytest
import yaml

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from nl2sql import NL2SQL, QueryEvent
from nl2sql.testing.facade import StubDatasources
from nl2sql.api.query_api import QueryResult, SubQueryResult
from nl2sql.cli.demo.playground.app import REPLAY_MISS_MESSAGE, build_app
from nl2sql.cli.demo.playground.hosted import KEY_HEADER, SESSION_COOKIE, Hosted, Limits, Replay
from nl2sql.llm.request_key import current_api_key

VISITOR_KEY = "-".join(["sk", "proj", "streamvisitor" + "c" * 24 + "2e8a"])


def parse_sse(text):
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "data" in fields:
            events.append((fields.get("event", "message"), json.loads(fields["data"])))
    return events


class _Engine(NL2SQL):
    """A facade whose run is scripted: ``result`` and the key each run saw."""

    def __init__(self, result=None):
        self.result = result or QueryResult(status="success", trace_id="t-1", sub_queries=[
            SubQueryResult(id="sq1", sql="SELECT 1", status="success")])
        self.seen = []

    @property
    def datasources(self):
        return StubDatasources(getattr(self, "_runtime", None), ["chinook"])

    def run_query(self, natural_language, datasource_id=None, execute=True, user_context=None,
                  cancellation_token=None):
        self.seen.append(("run", current_api_key(), user_context.roles))
        return self.result

    def stream_query(self, natural_language, datasource_id=None, execute=True, user_context=None,
                     cancellation_token=None):
        self.seen.append(("stream", current_api_key(), user_context.roles))
        yield QueryEvent(type="node_started", data={"node": "decomposer"})
        yield QueryEvent(type="node_finished", data={"node": "decomposer", "seconds": 0.01, "ok": True})
        yield QueryEvent(type="answer_delta", data={"field": "summary", "text": "One "})
        yield QueryEvent(type="final", data=self.result.model_dump(mode="json"))


def _app(engine, **kwargs):
    kwargs.setdefault("mode", "live")
    base_url = kwargs.pop("base_url", "http://testserver")
    return TestClient(build_app(engine, questions=["q1"], roles=["admin", "viewer"], dataset="chinook", **kwargs),
                      base_url=base_url)


def _stream(client, question="q1", role="viewer", headers=None):
    return client.post("/api/ask/stream", json={"question": question, "role": role}, headers=headers or {})


def test_the_stream_reports_the_run_and_ends_in_what_ask_returns():
    engine = _Engine()
    client = _app(engine)

    reply = _stream(client)

    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/event-stream")
    assert reply.headers["cache-control"] == "no-cache"
    events = parse_sse(reply.text)
    assert [name for name, _ in events] == ["node_started", "node_finished", "answer_delta", "final"]
    final = events[-1][1]
    asked = client.post("/api/ask", json={"question": "q1", "role": "viewer"}).json()
    assert final == asked
    assert final["replay_miss"] is False and final["recorded"] is False
    assert engine.seen[0] == ("stream", None, ["viewer"])


def test_a_streamed_replay_miss_is_rewritten_as_ask_rewrites_it():
    missing = QueryResult(status="error", errors=[{
        "node": "decomposer", "message": "Decomposition failed: fake llm: no rule for DecomposerResponse",
        "error_code": "ORCHESTRATOR_CRASH", "severity": "ERROR"}])
    client = _app(_Engine(missing), mode="replay")

    final = parse_sse(_stream(client).text)[-1]

    assert final[0] == "final"
    assert final[1]["replay_miss"] is True
    assert [e["message"] for e in final[1]["errors"]] == [REPLAY_MISS_MESSAGE]
    assert "fake llm" not in json.dumps(final[1])


def test_a_run_that_breaks_ends_in_an_error_event():
    class _Broken(_Engine):
        def stream_query(self, *args, **kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    events = parse_sse(_stream(_app(_Broken())).text)

    assert events == [("error", {"message": "The question could not be answered: boom",
                                 "error_code": "UNKNOWN_ERROR"})]


@pytest.fixture
def project(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "llm.demo.yaml").write_text(yaml.safe_dump({
        "version": 1,
        "default": {"provider": "openai", "model": "gpt-5.4", "api_key": "${env:OPENAI_API_KEY}",
                    "name": "default"},
        "agents": {},
    }), encoding="utf-8")
    return tmp_path


def _hosted(engine, project, limits=None, recorded=()):
    replay = Replay(base_url="http://127.0.0.1:9/v1", questions=list(recorded)) if recorded else None
    return _app(engine, mode="hosted", project_dir=project, host="0.0.0.0",
                hosted=Hosted(enabled=True, limits=limits, replay=replay), base_url="http://demo.example:7860")


def test_hosted_the_visitors_key_reaches_the_run_and_the_session_cookie_is_set(project):
    engine = _Engine()
    client = _hosted(engine, project)

    reply = _stream(client, headers={KEY_HEADER: VISITOR_KEY})

    assert reply.status_code == 200, reply.text
    assert engine.seen == [("stream", VISITOR_KEY, ["viewer"])]
    assert reply.cookies.get(SESSION_COOKIE)
    assert VISITOR_KEY not in reply.text
    assert current_api_key() is None


def test_hosted_limits_refuse_before_the_stream_starts(project):
    client = _hosted(_Engine(), project, limits=Limits(per_minute=50, per_session=1))

    first = _stream(client, headers={KEY_HEADER: VISITOR_KEY})
    again = _stream(client, headers={KEY_HEADER: VISITOR_KEY})

    assert first.status_code == 200
    assert again.status_code == 429
    assert again.headers["content-type"].startswith("application/json")
    assert again.json()["detail"]


def test_hosted_without_a_key_an_unrecorded_question_misses_without_running(project):
    engine = _Engine()
    client = _hosted(engine, project, recorded=["q1"])

    events = parse_sse(_stream(client, question="Something nobody recorded").text)

    assert [name for name, _ in events] == ["final"]
    assert events[0][1]["replay_miss"] is True
    assert [e["message"] for e in events[0][1]["errors"]] == [REPLAY_MISS_MESSAGE]
    assert engine.seen == []


def test_hosted_without_a_key_a_recorded_question_streams_its_recording(project):
    engine = _Engine()
    client = _hosted(engine, project, recorded=["q1"])

    final = parse_sse(_stream(client, question="q1").text)[-1][1]

    assert final["recorded"] is True and final["replay_miss"] is False
    assert engine.seen[0][0] == "stream"
