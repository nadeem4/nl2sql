"""The one place a Server-Sent Events response is built (docs/architecture/wave_d.md, D207).

``event_stream`` sends the facade's events as they come (each is ``Event.sse()``), a ``: keep-alive`` comment while
nothing does, and stops the run when the client goes away. ``frame`` and ``KEEP_ALIVE`` are the same wire for streams
the API makes itself (a job's progress).
"""
import asyncio
from typing import Callable, Iterator, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from nl2sql import CancellationToken, Event

HEARTBEAT_S = 15.0
KEEP_ALIVE = ": keep-alive\n\n"
# Proxies (nginx, a Space's) buffer a response unless told not to, which would hold every event back until the run ends.
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

_END = object()


def frame(name: str, data: str) -> str:
    """One Server-Sent Event: ``event: <name>`` and ``data: <json>``."""
    return f"event: {name}\ndata: {data}\n\n"


def event_stream(open_events: Callable[[], Iterator[Event]], request: Request, *,
                 token: Optional[CancellationToken] = None, heartbeat_s: float = HEARTBEAT_S) -> StreamingResponse:
    """Stream ``open_events()`` as SSE. The iterator runs in the thread pool, so a blocking facade call never holds the
    event loop. When the client disconnects (or the run is over) the iterator is closed, which cancels a run that
    cancels on close, and ``token`` is cancelled for one that takes a token."""

    async def body():
        iterator = open_events()
        pending = None
        try:
            while True:
                pending = asyncio.ensure_future(run_in_threadpool(next, iterator, _END))
                while True:
                    done, _ = await asyncio.wait({pending}, timeout=heartbeat_s)
                    if done:
                        break
                    if await request.is_disconnected():
                        return
                    yield KEEP_ALIVE
                item = pending.result()
                if item is _END:
                    return
                yield item.sse()
        finally:
            if token is not None:
                token.cancel()
            _close(iterator, pending)

    return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)


def _close(iterator, pending) -> None:
    """Close the iterator now, or when the step it is inside of returns (a running generator cannot be closed)."""
    close = getattr(iterator, "close", None)
    if close is None:
        return
    if pending is not None and not pending.done():
        pending.add_done_callback(lambda task: (task.cancelled() or task.exception(), close()))
    else:
        close()
