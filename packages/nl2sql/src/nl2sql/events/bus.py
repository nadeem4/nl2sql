"""``EventBus``: one engine's events, delivered synchronously to its subscribers.

The design is docs/architecture/wave_a.md, section 3, "The bus":

- ``publish`` calls each subscriber in subscription order, on the publishing
  thread, and returns. No queue, no worker.
- One bus per engine (``Runtime.bus``): two engines in one process share
  nothing.
- Subscribers fail open (D9): an exception is caught, logged once per
  subscriber and event name at WARNING, and counted in ``failures``. A
  subscriber made with ``fail="raise"`` (a run collector) propagates instead,
  since a broken collector is an engine bug a test must see.
- Sub-queries publish from LangGraph's worker threads at once, so the
  subscriber list is replaced, never changed in place.
- Only catalogued names, with data their model accepts.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, FrozenSet, Iterable, Literal, Mapping, Optional, Set, Tuple, Union

from pydantic import BaseModel

from nl2sql.events.catalog import CATALOG, check_names
from nl2sql_sdk.events import Event

logger = logging.getLogger("nl2sql.events")

Fail = Literal["open", "raise"]


class Subscription:
    """One subscriber on a bus. ``close()`` detaches it; calling it again does nothing."""

    def __init__(self, bus: "EventBus", fn: Callable[[Event], None], names: Optional[FrozenSet[str]],
                 fail: Fail, owner: Any) -> None:
        self._bus = bus
        self.fn = fn
        self.names = names
        self.fail = fail
        self.owner = owner  # what was subscribed: the function, or the observer whose on_event this is

    def wants(self, name: str) -> bool:
        return self.names is None or name in self.names

    def close(self) -> None:
        self._bus._remove(self)


class EventBus:
    """Synchronous, in-process publish and subscribe over the event catalog."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscriptions: Tuple[Subscription, ...] = ()
        self._warned: Set[Tuple[int, str]] = set()
        self.failures = 0

    def subscribe(self, fn: Union[Callable[[Event], None], Any], names: Optional[Iterable[str]] = None, *,
                  fail: Fail = "open") -> Subscription:
        """Call ``fn`` for every event, or for ``names`` only.

        ``fn`` is a function of one ``Event``, or an object with
        ``on_event`` (an ``Observer``); an object with ``close`` is closed
        when the bus closes. With no ``names``, an observer's own ``events``
        (when it has them) choose.

        Raises:
            ValueError: a name is not in the catalog.
        """
        call = getattr(fn, "on_event", None)
        if call is None:
            if not callable(fn):
                raise TypeError(f"subscribe takes a function or an object with on_event, not {type(fn).__name__}.")
            call = fn
        if names is None:
            names = getattr(fn, "events", None) if call is not fn else None
        subscription = Subscription(self, call, check_names(names) if names is not None else None, fail, fn)
        with self._lock:
            self._subscriptions = self._subscriptions + (subscription,)
        return subscription

    def publish(self, name: str, data: Union[BaseModel, Mapping[str, Any], None] = None, *,
                trace_id: Optional[str] = None, tenant_id: Optional[str] = None) -> Event:
        """Validate ``data`` against ``name``'s model, build the event and hand it to each subscriber.

        Raises:
            ValueError: ``name`` is not in the catalog (an engine bug).
            pydantic.ValidationError: ``data`` does not fit the name's model.
        """
        spec = CATALOG.get(name)
        if spec is None:
            raise ValueError(f"'{name}' is not in the event catalog (nl2sql/events/catalog.py).")
        if isinstance(data, spec.model):
            model = data
        else:
            if isinstance(data, BaseModel):
                data = data.model_dump()
            model = spec.model.model_validate(dict(data or {}))
        event = Event(name=name, at=datetime.now(timezone.utc), trace_id=trace_id, tenant_id=tenant_id,
                      data=model.model_dump(mode="json", by_alias=True))
        for subscription in self._subscriptions:
            if subscription.wants(name):
                self._deliver(subscription, event)
        return event

    def _deliver(self, subscription: Subscription, event: Event) -> None:
        if subscription.fail == "raise":
            subscription.fn(event)
            return
        try:
            subscription.fn(event)
        except Exception as exc:
            with self._lock:
                self.failures += 1
                key = (id(subscription), event.name)
                first = key not in self._warned
                self._warned.add(key)
            if first:
                logger.warning(f"An event subscriber ({_label(subscription.owner)}) failed on {event.name} "
                               f"and was skipped: {type(exc).__name__}: {exc}")

    def _remove(self, subscription: Subscription) -> None:
        with self._lock:
            self._subscriptions = tuple(s for s in self._subscriptions if s is not subscription)

    def close(self) -> None:
        """Detach every subscriber and close each one that has ``close`` (the observers it holds)."""
        with self._lock:
            subscriptions, self._subscriptions = self._subscriptions, ()
        for subscription in subscriptions:
            close = getattr(subscription.owner, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.warning(f"Closing {_label(subscription.owner)} failed: {exc}")


def _label(owner: Any) -> str:
    return getattr(owner, "__qualname__", None) or type(owner).__name__
