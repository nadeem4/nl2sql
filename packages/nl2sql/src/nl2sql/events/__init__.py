"""The engine's events: the bus, the catalog, and the run's scope.

``EventBus`` (one per engine, ``Runtime.bus``) delivers every event; the
catalog (``nl2sql.events.catalog``) lists each name with a model for its
data; ``RunScope`` is one question on the bus, and ``publish`` is how code
inside a run publishes. ``Event`` and ``Observer`` themselves live in the SDK
(``nl2sql_sdk.events``), so an observer plugin depends on the SDK alone. The
design is docs/architecture/wave_a.md, section 3.
"""
from nl2sql.events.bus import EventBus, Subscription
from nl2sql.events.catalog import (AUDIT_EVENTS, CATALOG, RUN_EVENTS, STREAM_EVENTS, EventInfo, RunStatus,
                                   catalog)
from nl2sql.events.run import RunScope, current_scope, publish
from nl2sql_sdk.events import Event, Observer

__all__ = ["AUDIT_EVENTS", "CATALOG", "RUN_EVENTS", "STREAM_EVENTS", "Event", "EventBus", "EventInfo", "Observer",
           "RunScope", "RunStatus", "Subscription", "catalog", "current_scope", "publish"]
