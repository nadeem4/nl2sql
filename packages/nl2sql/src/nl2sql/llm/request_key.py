"""The API key one request brought with it, for a server that keeps none.

A public demo cannot hold a key of its own: every visitor brings theirs, it is
used for their question and then it is gone. :func:`use_api_key` binds a key to
the work of one request and :class:`~nl2sql.llm.registry.LLMRegistry` builds a
client from it, caching nothing, so no second request can be handed a client
built with someone else's key.

A :class:`~contextvars.ContextVar` is the store because the engine runs one
question per thread: Starlette dispatches the playground's synchronous
``/api/ask`` into its threadpool, and the pipeline's LLM clients are all built
while the graph is constructed, in that same thread. Nothing here is global,
so two visitors asking at once never see each other's key.

:func:`current_api_key` is also read by the trace redactor
(:func:`nl2sql.tracing.trace.collect_secrets`), so a key that reached a prompt
or an error is masked in the trace like any other credential.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_request_api_key: ContextVar[Optional[str]] = ContextVar("nl2sql_request_api_key", default=None)


@contextmanager
def use_api_key(key: Optional[str]) -> Iterator[None]:
    """Binds ``key`` to the current context for the duration of the block."""
    token = _request_api_key.set((key or "").strip() or None)
    try:
        yield
    finally:
        _request_api_key.reset(token)


def current_api_key() -> Optional[str]:
    """The key bound to this context, or None when the caller brought none."""
    return _request_api_key.get()
