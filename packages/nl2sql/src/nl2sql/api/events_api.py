"""``engine.events``: the operator's view of every run and of the engine.

See docs/architecture/wave_a.md, section 3, "The facade", and
docs/observability/events.md.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, List, Optional, Union

from nl2sql.events import EventInfo, Subscription, catalog
from nl2sql_sdk.events import Event, Observer


class EventsAPI:
    """The engine's events. Internal: reach it through ``NL2SQL.events``."""

    def __init__(self, ctx: Any):
        self._ctx = ctx

    def subscribe(self, fn: Union[Callable[[Event], None], Observer],
                  names: Optional[Iterable[str]] = None) -> Subscription:
        """Call ``fn`` for every event this engine publishes, or for ``names`` only.

        Every run's events (``run.*``, ``node.*``, ``llm.call``,
        ``policy.decision``, ``hook.decision`` and the rest: the operator
        data the caller's own stream leaves out) and the engine's own (an index
        build, a purge, an LLM swap). ``fn`` is a function of one
        :class:`~nl2sql.Event` or an :class:`~nl2sql.Observer`; with no
        ``names``, an observer's own ``events`` choose. It is called
        synchronously on the publishing thread, which may be one of a run's
        worker threads, so a slow subscriber slows the run. Subscribers fail
        open: an exception is logged once and skipped, never failing a run.
        ``Subscription.close()`` detaches it.

        Raises:
            ValueError: a name is not in the catalog (``catalog()``).
        """
        return self._ctx.bus.subscribe(fn, names)

    def catalog(self) -> List[EventInfo]:
        """Every event the engine publishes: its name, description, whether a run has it, whether the
        caller's stream carries it, whether the audit observer writes it, and its data's JSON Schema."""
        return catalog()
