"""``engine.events``: the operator's view of every run and of the engine.

A stub in the foundation PR, so the namespace exists for every track; E1
adds ``subscribe(fn, names=None)`` and ``catalog()``. See
docs/architecture/wave_a.md, section 3, "The facade".
"""
from __future__ import annotations

from typing import Any


class EventsAPI:
    """The engine's events. Internal: reach it through ``NL2SQL.events``."""

    def __init__(self, ctx: Any):
        self._ctx = ctx
