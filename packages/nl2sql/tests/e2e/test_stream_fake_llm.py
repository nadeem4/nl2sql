"""A question streamed end to end on the demo databases, with no key.

``stream_query`` reports every step as it runs, the result manifest as soon as
the results are saved, the answer as the model writes it (the fake server
streams the recorded tool call a word at a time), and ends in exactly what
``run_query`` returns. ``nl2sql-api`` serves it as Server-Sent Events.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from .test_answer_preview_fake_llm import ANSWER, QUESTION, RULES  # noqa: E402
from .test_result_paging_fake_llm import _env  # noqa: E402


def parse_sse(text):
    """Each event's name and its ``data``; the SSE ``data`` line is the whole event."""
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "data" in fields:
            event = json.loads(fields["data"])
            events.append((event["name"], event["data"]))
    return events


def _comparable(body):
    """A run's result without what differs between two runs of the same question."""
    usage = body["usage"]["total"]
    return {
        "status": body["status"], "final_answer": body["final_answer"], "errors": body["errors"],
        "truncated": body["truncated"],
        "sub_queries": body["sub_queries"],
        "result_manifest": body["result_manifest"],
        "usage": {k: usage[k] for k in ("calls", "input_tokens", "output_tokens", "total_tokens")},
        "steps": sorted(k for k in body["timings"]),
    }


def _check_progress(events):
    """What a streamed run reports, in the order it must report it."""
    names = [name for name, _ in events]
    assert names[0] == "run.started"
    started = [d["node"] for n, d in events if n == "node.started"]
    finished = [d["node"] for n, d in events if n == "node.finished"]
    assert started[0] == "datasource_resolver"
    for step in ("decomposer", "ast_planner", "logical_validator", "generator", "executor", "aggregator",
                 "answer_synthesizer"):
        assert step in started and step in finished, step
    manifest = names.index("result.manifest")
    # The rows can load before the answer is written.
    answer_starts = [i for i, (n, d) in enumerate(events) if n == "node.started" and d["node"] == "answer_synthesizer"]
    assert manifest < answer_starts[0]
    deltas = [d for n, d in events if n == "answer.delta"]
    assert len([d for d in deltas if d["field"] == "summary"]) > 2
    assert "".join(d["text"] for d in deltas if d["field"] == "summary") == ANSWER["summary"]
    assert "".join(d["text"] for d in deltas if d["field"] == "content") == ANSWER["content"]
    assert names[-1] == "run.finished" and names.count("run.finished") == 1 and "run.failed" not in names
    # Never operator data on the caller's stream.
    assert not set(names) & {"policy.decision", "hook.decision", "llm.call", "plan_cache.lookup"}
    final = events[-1][1]["result"]
    assert events[-1][1]["status"] == final["status"]
    assert events[manifest][1]["results"] == final["result_manifest"]
    return final


@pytest.mark.e2e
def test_the_engine_streams_a_run_that_ends_as_run_query_ends(demo_project, fake_llm, monkeypatch):
    from nl2sql import NL2SQL, UserContext

    server, env = fake_llm(RULES)
    _env(monkeypatch, demo_project, env)
    admin = UserContext(roles=["admin"])
    engine = NL2SQL.from_config()
    streamed = list(engine.stream_query(QUESTION, user_context=admin))
    plain = engine.run_query(QUESTION, user_context=admin).model_dump(mode="json")

    final = _check_progress([(e.name, e.data) for e in streamed])
    # Every event is this run's; the manifest's trace_id is the request_id the result routes take.
    assert {e.trace_id for e in streamed} == {final["trace_id"]}
    assert final["status"] == "success", final["errors"]
    assert _comparable(final) == _comparable(plain)
    # Only the answer model was asked to stream.
    streamed = [c["name"] for c in server.calls if c["body"].get("stream")]
    assert streamed == ["AggregatedResponse"]


@pytest.mark.e2e
def test_the_rest_api_streams_server_sent_events(demo_project, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient

    pytest.importorskip("nl2sql_api")
    from nl2sql_api.main import app

    server, env = fake_llm(RULES)
    _env(monkeypatch, demo_project, env)
    with TestClient(app) as client:
        reply = client.post("/api/v1/query/stream", json={"question": QUESTION})

    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/event-stream")
    final = _check_progress(parse_sse(reply.text))
    assert final["status"] == "success", final["errors"]
