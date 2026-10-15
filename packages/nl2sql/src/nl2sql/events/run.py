"""``RunScope``: the life of one question on the bus, and the module function ``publish``.

The design is docs/architecture/wave_a.md, section 3, "Run events go through
the run's scope":

1. The caller that builds the result opens it (``QueryAPI.run_query`` and
   ``stream_query``, and the eval runner): ``RunScope(...)``, ``attach`` the
   run-scoped pieces, then ``start()``, which publishes ``run.started``. The
   scope makes the run's ``trace_id``; ``run_with_graph`` puts it in the
   graph state.
2. Its context is entered inside the worker (``run_with_graph``'s
   ``_invoke``), beside the policy engine's run, so LangGraph's own worker
   threads inherit it. Code deep in a run publishes with
   ``nl2sql.events.publish(name, data)``, which reads the scope from the
   context and does nothing outside one. The same context sets the
   ``trace_id`` and ``tenant_id`` every log line carries
   (``TraceContextFilter``).
3. It closes exactly once: ``finish(result)`` publishes ``run.finished`` (a
   summary, never rows), ``fail(message, code)`` publishes ``run.failed``.
4. A closed scope drops what comes after (a timed-out run's graph winding
   down) and counts it in ``late_events``.

``attach`` is where a later PR plugs in a run-scoped piece (the stream's
queue, the budget, the trace's event list) without editing ``run_with_graph``
or ``QueryAPI``. ``callbacks()`` is the usage collector and the handlers
attached, for a model call made outside the graph but inside the run.
"""
from __future__ import annotations

import contextvars
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, List, Mapping, Optional, Union

from pydantic import BaseModel

from nl2sql.common.logger import tenant_context, trace_context
from nl2sql.events.bus import EventBus, Subscription
from nl2sql.events.catalog import RunFinished, RunStarted
from nl2sql_sdk.events import Event

_current: contextvars.ContextVar[Optional["RunScope"]] = contextvars.ContextVar("nl2sql_run_scope", default=None)


def current_scope() -> Optional["RunScope"]:
    """The ``RunScope`` of the run this code is part of, or None outside a run."""
    return _current.get()


def publish(name: str, data: Union[BaseModel, Mapping[str, Any], None] = None) -> Optional[Event]:
    """Publish a run event on the current run's scope. Outside a ``RunScope`` it does nothing.

    For code inside a run only (the structured-output retry, the hook runner,
    the budget, the enforcement points). Code outside a question publishes
    with ``ctx.bus.publish``.
    """
    scope = _current.get()
    return scope.publish(name, data) if scope is not None else None


def _is_callback_handler(piece: Any) -> bool:
    from langchain_core.callbacks import BaseCallbackHandler

    return isinstance(piece, BaseCallbackHandler)


class RunScope:
    """One question on the bus: its ``trace_id``, its run-scoped pieces and its two ends."""

    def __init__(self, bus: EventBus, *, question: str, user_context: Any = None,
                 datasource_id: Optional[str] = None, execute: bool = True, tenant_id: Optional[str] = None,
                 trace_id: Optional[str] = None, usage: Any = None) -> None:
        self.bus = bus
        self.trace_id = trace_id or str(uuid.uuid4())
        self.question = question
        self.user_id: Optional[str] = getattr(user_context, "user_id", None)
        self.roles: List[str] = list(getattr(user_context, "roles", None) or [])
        self.tenant_id = getattr(user_context, "tenant_id", None) or tenant_id
        self.datasource_id = datasource_id
        self.execute = execute
        # The run's usage collector (a ``TokenUsageCallback``), when the caller made one: every
        # model call made with ``callbacks()`` counts in the run's ``QueryResult.usage``.
        self.usage = usage
        self._handlers: List[Any] = []
        self.late_events = 0
        self._lock = threading.RLock()
        self._closed = False
        self._started_at: Optional[float] = None
        self._subscriptions: List[Subscription] = []
        self._closers: List[Callable[[], None]] = []

    @property
    def closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------ pieces

    def attach(self, piece: Any, *, names: Optional[Iterable[str]] = None) -> Any:
        """Plug a run-scoped piece into this run; it is detached when the scope closes.

        - A function of one ``Event``, or an object with ``on_event``, receives
          this run's events (``names`` only, when given), in publish order. It
          subscribes with ``fail="raise"``: a run collector that breaks is an
          engine bug.
        - A LangChain callback handler (the budget) joins the graph's callbacks.
        - An object with ``close`` is closed when the scope closes.

        Returns ``piece``.
        """
        handler = _is_callback_handler(piece)
        if handler:
            self._handlers.append(piece)
        receive = getattr(piece, "on_event", None) or (piece if callable(piece) and not handler else None)
        if receive is not None:
            trace_id = self.trace_id

            def deliver(event: Event) -> None:
                if event.trace_id == trace_id:
                    receive(event)

            wanted = names if names is not None else getattr(piece, "events", None)
            self._subscriptions.append(self.bus.subscribe(deliver, wanted, fail="raise"))
        close = getattr(piece, "close", None)
        if callable(close):
            self._closers.append(close)
        return piece

    def callbacks(self) -> List[Any]:
        """The LangChain callbacks a model call in this run is made with: the usage collector, then
        the handlers attached (the budget).

        ``run_with_graph`` adds them to the graph's; a call made outside the
        graph (Wave C's rewrite) passes them itself, inside ``entered()``, so
        it counts in the run's usage and limits and carries its ``trace_id``.
        """
        return ([self.usage] if self.usage is not None else []) + list(self._handlers)

    # ------------------------------------------------------------ the run's life

    def start(self) -> Event:
        """Publish ``run.started``. Call it once the pieces are attached."""
        self._started_at = time.perf_counter()
        return self.publish("run.started", RunStarted(
            question=self.question, user_id=self.user_id, tenant_id=self.tenant_id, roles=self.roles,
            datasource_id=self.datasource_id, execute=self.execute))

    def publish(self, name: str, data: Union[BaseModel, Mapping[str, Any], None] = None) -> Optional[Event]:
        """Publish a run event with this run's ``trace_id``; after the scope closed, drop and count it."""
        with self._lock:
            if self._closed:
                self.late_events += 1
                return None
            return self.bus.publish(name, data, trace_id=self.trace_id, tenant_id=self.tenant_id)

    @contextmanager
    def entered(self) -> Iterator["RunScope"]:
        """This scope as the current one, with its ``trace_id`` and ``tenant_id`` on every log line."""
        token = _current.set(self)
        try:
            with trace_context(self.trace_id), tenant_context(self.tenant_id):
                yield self
        finally:
            _current.reset(token)

    def finish(self, result: Any, *, tables: Iterable[str] = ()) -> Optional[Event]:
        """Close with ``run.finished``: ``result``'s summary, never its rows. A second close does nothing.

        ``result`` is the run's ``QueryResult``; ``tables`` are the tables it
        read, as ``datasource.schema.table``.
        """
        return self._close("run.finished", RunFinished(
            status=result.status,
            duration_s=self._duration(),
            usage=result.usage.total.model_dump(),
            error_codes=[str(error.get("error_code") or "") for error in result.errors],
            source=result.source.type,
            datasources=sorted({sq.datasource_id for sq in result.sub_queries if sq.datasource_id}),
            tables=sorted(set(tables)),
            result_manifest=[summary.model_dump(mode="json") for summary in result.result_manifest],
            user_id=self.user_id,
            roles=self.roles,
        ))

    def fail(self, message: str, error_code: str = "UNKNOWN_ERROR") -> Optional[Event]:
        """Close with ``run.failed``: the run could not start, or crashed outside the pipeline."""
        return self._close("run.failed", {"message": message, "error_code": str(getattr(error_code, "value",
                                                                                         error_code))})

    def _duration(self) -> float:
        return round(time.perf_counter() - self._started_at, 4) if self._started_at is not None else 0.0

    def _close(self, name: str, data: Any) -> Optional[Event]:
        with self._lock:
            if self._closed:
                return None
            try:
                event = self.bus.publish(name, data, trace_id=self.trace_id, tenant_id=self.tenant_id)
            finally:
                self._closed = True
                subscriptions, self._subscriptions = self._subscriptions, []
                closers, self._closers = self._closers, []
        for subscription in subscriptions:
            subscription.close()
        for close in closers:
            close()
        return event
