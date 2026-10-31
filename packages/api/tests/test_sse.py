"""``nl2sql_api.sse.event_stream``: one place an SSE response is built (docs/architecture/wave_d.md, D207)."""
import asyncio
import threading
from datetime import datetime, timezone

from nl2sql import CancellationToken, Event
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
