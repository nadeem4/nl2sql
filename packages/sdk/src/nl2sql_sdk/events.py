"""The event contract: ``Event``, what the engine publishes, and ``Observer``, what receives it.

An observer plugin depends on this module alone (pydantic and the standard
library), not on the engine. The names an engine publishes, and the shape of
each one's ``data``, are its event catalog (``engine.events.catalog()``).
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, FrozenSet, Optional, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class Event(BaseModel):
    """One thing that happened in an engine: a run's step, a model call, an index build.

    ``trace_id`` is the run's id (``QueryResult.trace_id``), so an event, a
    log line, a trace and a result page join on one value; it is None for an
    engine event no run caused (an index build). ``data`` is JSON-ready: the
    catalog's model for ``name``, dumped. It never holds result rows, sample
    values, keys or header values.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    at: datetime  # UTC
    trace_id: Optional[str] = None
    tenant_id: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

    def sse(self) -> str:
        """The event as one Server-Sent Event: ``event: <name>``, and the whole event as JSON in ``data``."""
        return f"event: {self.name}\ndata: {json.dumps(self.model_dump(mode='json'), default=str)}\n\n"


@runtime_checkable
class Observer(Protocol):
    """Contract for an observer: code that receives an engine's events (a log, an audit trail, a collector).

    An observer is named by ``type`` in the ``observers`` section, for
    example ``type: corp_siem``. The engine validates the rest of that block
    with ``config_model`` and builds the observer with ``from_config``, giving
    it ``folder`` (``storage.path``) for anything it writes. An object passed
    in code is used as given.

    ``events`` is the names it wants; None means every event. ``on_event`` is
    called synchronously on the publishing thread, which may be one of a
    run's worker threads, so an observer that keeps state locks it, and a slow
    one slows the run. Observers fail open: an exception from ``on_event`` is
    logged and skipped, and never fails a run. ``close`` is called when the
    engine closes.

    ``interface_version`` is the version of this contract the observer was
    written for. The engine accepts exactly the version it was built for (1).
    """

    interface_version: ClassVar[int]
    config_model: ClassVar[type[BaseModel]]
    events: ClassVar[Optional[FrozenSet[str]]]

    @classmethod
    def from_config(cls, cfg: BaseModel, *, folder: Path) -> Self:
        """Build the observer from a validated ``config_model`` instance."""
        ...

    def on_event(self, event: Event) -> None:
        """Receive one event."""
        ...

    def close(self) -> None:
        """Flush and release what the observer holds."""
        ...
