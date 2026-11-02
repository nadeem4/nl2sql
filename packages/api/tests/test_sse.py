"""``nl2sql_api.sse.event_stream``: one place an SSE response is built (docs/architecture/wave_d.md, D207)."""
import asyncio
import json
import threading
from datetime import datetime, timezone

from nl2sql import CancellationToken, ConversationBusy, Event
from nl2sql_api.sse import KEEP_ALIVE, SSE_HEADERS, event_stream


class _Request:
    """Stands in for the Starlette request; ``gone`` is the client closing the connection."""

    def __init__(self):
        self.gone = False

    async def is_disconnected(self):
        return self.gone


def _event(name):
    return Event(name=name, at=datetime(2026, 1, 1, tzinfo=timezone.utc), data={})


def test_sse_heartbeat_and_close_on_disconnect():
    release, closed, token, request = threading.Event(), threading.Event(), CancellationToken(), _Request()

    def events():
        try:
            yield _event("run.started")
            release.wait(5)  # the planner is thinking: nothing to send
            yield _event("run.finished")
        finally:
            closed.set()

    async def go():
        response = event_stream(events, request, token=token, heartbeat_s=0.02)
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache" and response.headers["x-accel-buffering"] == "no"
        assert SSE_HEADERS["Cache-Control"] == "no-cache"
        body = response.body_iterator
        assert await body.__anext__() == _event("run.started").sse()
        assert await body.__anext__() == KEEP_ALIVE  # a quiet stream still sends something
        request.gone = True
        async for chunk in body:  # ends once the disconnect is seen
            assert chunk == KEEP_ALIVE
        assert token.is_cancelled()
        release.set()
        await asyncio.sleep(0.2)  # the loop must outlive the step the iterator was inside of

    asyncio.run(go())
    assert closed.wait(5), "the facade's iterator was not closed"


def test_sse_sends_every_event_then_ends():
    async def go():
        response = event_stream(lambda: iter([_event("a"), _event("b")]), _Request())
        return [chunk async for chunk in response.body_iterator]

    assert asyncio.run(go()) == [_event("a").sse(), _event("b").sse()]


def _failing(exc):
    def events():
        yield _event("run.started")
        raise exc
    return events


def _frames(events):
    async def go():
        response = event_stream(events, _Request())
        return [chunk async for chunk in response.body_iterator]
    return asyncio.run(go())


def test_sse_a_failure_mid_stream_is_a_final_run_failed_frame():
    chunks = _frames(_failing(RuntimeError("secret host db.internal")))

    assert chunks[0] == _event("run.started").sse()
    assert len(chunks) == 2 and chunks[1].startswith("event: run.failed\n")
    data = json.loads(chunks[1].split("data: ", 1)[1])["data"]
    assert data["error_code"] == "UNKNOWN_ERROR" and "secret" not in chunks[1], "never the exception text (D202)"


def test_sse_a_named_error_mid_stream_keeps_its_code_and_message():
    chunks = _frames(_failing(ConversationBusy("A turn is already running.")))

    data = json.loads(chunks[-1].split("data: ", 1)[1])["data"]
    assert chunks[-1].startswith("event: run.failed\n")
    assert data == {"message": "A turn is already running.", "error_code": "CONFLICT"}
