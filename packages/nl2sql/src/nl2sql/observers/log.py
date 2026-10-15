"""The ``log`` observer: one log line per event on logger ``nl2sql.events``.

The line is the event's name and a short summary of its data. ``node.started``
and ``answer.delta`` (one line per step, one per token) are logged only when
``level`` is ``DEBUG``, unless ``events`` names them. The run's ``trace_id``
reaches the line through the context (``TraceContextFilter``), as it reaches
every line the engine writes during a run. See docs/configuration/observers.md.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar, FrozenSet, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from nl2sql.events.catalog import CATALOG, check_names
from nl2sql_sdk.events import Event

logger = logging.getLogger("nl2sql.events")

# Logged only at DEBUG, unless ``events`` names them.
CHATTY = frozenset({"node.started", "answer.delta"})
_MAX_VALUE = 80


class LogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    events: Optional[List[str]] = None  # None: every event, but CHATTY below DEBUG

    @field_validator("events")
    @classmethod
    def _catalogued(cls, names: Optional[List[str]]) -> Optional[List[str]]:
        if names is not None:
            check_names(names)
        return names


def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= _MAX_VALUE else text[:_MAX_VALUE - 3] + "..."


def summary(event: Event) -> str:
    """``name key=value ...``: the event's data, empty values left out and long ones cut."""
    parts = [f"{key}={_short(value)}" for key, value in event.data.items() if value not in (None, "", [], {})]
    return " ".join([event.name, *parts])


class LogObserver:
    """Logs each event it receives at ``level``."""

    interface_version: ClassVar[int] = 1
    config_model: ClassVar[type[BaseModel]] = LogConfig
    events: Optional[FrozenSet[str]]

    def __init__(self, config: LogConfig) -> None:
        self.level = logging.getLevelName(config.level)
        if config.events is not None:
            self.events = frozenset(config.events)
        elif config.level == "DEBUG":
            self.events = None
        else:
            self.events = frozenset(CATALOG) - CHATTY

    @classmethod
    def from_config(cls, cfg: LogConfig, *, folder: Path) -> "LogObserver":
        return cls(cfg)

    def on_event(self, event: Event) -> None:
        if logger.isEnabledFor(self.level):
            logger.log(self.level, summary(event))

    def close(self) -> None:
        pass
