"""The ``files`` trace store: one JSON document per run in ``<storage.path>/traces/``.

Each document is ``<UTC timestamp>Z_<trace_id>.json``, written to a temporary
file and renamed, so a reader never sees half of one. ``max_age_days``
(default 14) is how long ``engine.storage.purge()`` keeps them.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field


class FilesConfig(BaseModel):
    """The ``files`` store's keys."""

    model_config = ConfigDict(extra="forbid")

    max_age_days: int = Field(14, ge=1)


class FilesTraces:
    interface_version: ClassVar[int] = 4
    slot: ClassVar[str] = "traces"
    config_model: ClassVar[type] = FilesConfig

    def __init__(self, directory: Path, max_age_days: int = 14) -> None:
        self.directory = Path(directory)
        self.max_age_days = max_age_days

    @classmethod
    def from_config(cls, cfg: FilesConfig, *, folder: Path) -> "FilesTraces":
        return cls(Path(folder) / "traces", cfg.max_age_days)

    def write(self, trace_id: str, document: dict) -> None:
        self.write_file(trace_id, document)

    def write_file(self, trace_id: str, document: dict) -> Path:
        """``write``, returning the file written: the run reports it as its ``trace_path``."""
        from nl2sql.tracing.document import write_trace

        return write_trace({**document, "trace_id": trace_id}, self.directory)

    def get(self, trace_id: str) -> Optional[dict]:
        """The newest document for ``trace_id``, or None.

        Raises:
            ValueError: ``trace_id`` is not a plain token, or the file is not a trace.
        """
        from nl2sql.tracing.document import find_trace, load_trace

        path = find_trace(trace_id, self.directory)
        return load_trace(path) if path is not None else None

    def purge(self, older_than: datetime) -> int:
        """Delete the trace files last written before ``older_than``; return how many."""
        if not self.directory.is_dir():
            return 0
        cutoff = older_than.timestamp()
        deleted = 0
        for path in self.directory.glob("*.json"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    os.remove(path)
                    deleted += 1
            except OSError:  # being read, or already gone: the next purge gets it
                continue
        return deleted
