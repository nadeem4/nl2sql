"""A question streamed end to end on the demo databases, with no key.

``stream_query`` reports every step as it runs, the result manifest as soon as
the results are saved, the answer as the model writes it (the fake server
streams the recorded tool call a word at a time), and ends in exactly what
``run_query`` returns. Both apps serve it as Server-Sent Events. Last, every
guided question the hosted Space replays without a key still replays when it
is streamed.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from .test_answer_preview_fake_llm import ANSWER, QUESTION, RULES  # noqa: E402
from .test_result_paging_fake_llm import _env  # noqa: E402


def parse_sse(text):
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "data" in fields:
            events.append((fields.get("event", "message"), json.loads(fields["data"])))
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
    started = [d["node"] for n, d in events if n == "node_started"]
    finished = [d["node"] for n, d in events if n == "node_finished"]
    assert started[0] == "datasource_resolver"
    for step in ("decomposer", "ast_planner", "logical_validator", "generator", "executor", "aggregator",
                 "answer_synthesizer"):
        assert step in started and step in finished, step
    manifest = names.index("result_manifest")
    # The rows can load before the answer is written.
    answer_starts = [i for i, (n, d) in enumerate(events) if n == "node_started" and d["node"] == "answer_synthesizer"]
    assert manifest < answer_starts[0]
    deltas = [d for n, d in events if n == "answer_delta"]
    assert len([d for d in deltas if d["field"] == "summary"]) > 2
    assert "".join(d["text"] for d in deltas if d["field"] == "summary") == ANSWER["summary"]
    assert "".join(d["text"] for d in deltas if d["field"] == "content") == ANSWER["content"]
    assert names[-1] == "final" and names.count("final") == 1 and "error" not in names
    final = events[-1][1]
    assert events[manifest][1]["trace_id"] == final["trace_id"]
    assert events[manifest][1]["results"] == final["result_manifest"]
    return final


@pytest.mark.e2e
def test_the_engine_streams_a_run_that_ends_as_run_query_ends(demo_project, fake_llm, monkeypatch):
    from nl2sql import NL2SQL, UserContext
    from nl2sql.common.settings import reload_settings

    server, env = fake_llm(RULES)
    _env(monkeypatch, demo_project, env)
    admin = UserContext(roles=["admin"])
    try:
        engine = NL2SQL()
        events = [(e.type, e.data) for e in engine.stream_query(QUESTION, user_context=admin)]
        plain = engine.run_query(QUESTION, user_context=admin).model_dump(mode="json")
    finally:
        reload_settings()

    final = _check_progress(events)
    assert final["status"] == "success", final["errors"]
    assert _comparable(final) == _comparable(plain)
    # Only the answer model was asked to stream.
    streamed = [c["name"] for c in server.calls if c["body"].get("stream")]
    assert streamed == ["AggregatedResponse"]


@pytest.mark.e2e
def test_the_rest_api_streams_server_sent_events(demo_project, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient

    pytest.importorskip("nl2sql_api")
    from nl2sql.common.settings import reload_settings
    from nl2sql_api.main import app

    server, env = fake_llm(RULES)
    _env(monkeypatch, demo_project, env)
    try:
        with TestClient(app) as client:
            reply = client.post("/api/v1/query/stream", json={"natural_language": QUESTION})
    finally:
        reload_settings()

    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/event-stream")
    final = _check_progress(parse_sse(reply.text))
    assert final["status"] == "success", final["errors"]


@pytest.mark.e2e
def test_the_playground_streams_server_sent_events(demo_project, fake_llm, monkeypatch):
    from fastapi.testclient import TestClient

    from nl2sql import NL2SQL
    from nl2sql.cli.demo.playground.app import build_app
    from nl2sql.common.settings import reload_settings

    server, env = fake_llm(RULES)
    _env(monkeypatch, demo_project, env)
    try:
        client = TestClient(build_app(NL2SQL(), questions=[], roles=["admin"], mode="live", dataset="chinook"))
        reply = client.post("/api/ask/stream", json={"question": QUESTION, "role": "admin"})
        final = _check_progress(parse_sse(reply.text))
        [entry] = final["result_manifest"]
        page = client.get(f"/api/query/{final['trace_id']}/results/{entry['node_id']}").json()
    finally:
        reload_settings()

    assert final["status"] == "success" and final["recorded"] is False
    assert len(page["rows"]) == 25


@pytest.mark.e2e
def test_every_guided_question_replays_when_streamed_with_no_key(demo_project, tmp_path, monkeypatch):
    """The hosted Space, keyless, streaming each guided question from the packaged recordings."""
    import shutil

    from fastapi.testclient import TestClient

    from nl2sql import NL2SQL
    from nl2sql.cli.commands.demo import REPLAY_STREAM_DELAY, replay_recordings
    from nl2sql.datasets.demo import DEMO_QUESTIONS
    from nl2sql.cli.demo.llm_config import point_llm_config_at
    from nl2sql.cli.demo.playground.app import build_app
    from nl2sql.cli.demo.playground.hosted import Hosted, Limits, Replay
    from nl2sql.common.settings import reload_settings
    from nl2sql.llm.replay import ReplayStore
    from nl2sql.testing.fake_llm import FakeLLMServer

    project = tmp_path / "demo"
    shutil.copytree(demo_project, project)
    store = ReplayStore.load(replay_recordings(project / "nowhere"))
    assert set(store.covered(DEMO_QUESTIONS)) == set(DEMO_QUESTIONS)

    for name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    point_llm_config_at(project, None, provider="openai")
    monkeypatch.chdir(project)
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.setenv("LLM_CONFIG", "configs/llm.demo.yaml")
    # As the Space runs it: with the demo's verified queries.
    monkeypatch.setenv("CURATED_ENABLED", "true")
    reload_settings()
    replay = FakeLLMServer(store.rules(), stream_delay=REPLAY_STREAM_DELAY / 10).start()
    finals = {}
    try:
        hosted = Hosted(enabled=True, limits=Limits(per_minute=100, per_session=100), replay=Replay(replay.base_url, store.covered(DEMO_QUESTIONS),
                                                    store.recorded_with))
        app = build_app(NL2SQL(), questions=list(DEMO_QUESTIONS), roles=["admin"], mode="hosted",
                        dataset="chinook", project_dir=project, host="0.0.0.0", hosted=hosted)
        client = TestClient(app, base_url="http://demo.example:7860")
        for question in DEMO_QUESTIONS:
            events = parse_sse(client.post("/api/ask/stream", json={"question": question, "role": "admin"}).text)
            finals[question] = (events[-1], [n for n, _ in events].count("answer_delta"))
    finally:
        replay.stop()
        reload_settings()

    missed = [q for q, ((name, body), _) in finals.items() if name != "final" or body["replay_miss"]]
    assert not missed, missed
    failed = {q: body["errors"] for q, ((_, body), _) in finals.items() if body["status"] != "success"}
    assert not failed, failed
    assert all(body["recorded"] for (_, body), _ in finals.values())
    # Each recorded answer streamed in pieces, not as one block.
    assert all(deltas > 2 for _, deltas in finals.values())
    # Every recorded call answered. The curated matcher's choice is the one
    # exception until the demo is recorded again: recordings made before it
    # hold no VerifiedQueryChoice, so it misses and the run falls back to the
    # recorded generated answer, as asserted above.
    unrecorded = {c["name"] for c in replay.calls if not c["matched"]}
    assert unrecorded <= {"VerifiedQueryChoice"}, unrecorded
