"""The ``audit`` observer: who did what, as JSON lines under ``<storage.path>/audit/`` (D19, B27).

One line per audited event, in ``<storage.path>/audit/<date>.jsonl`` (the
event's UTC date): the event itself, ``name``, ``at``, ``trace_id``,
``tenant_id`` and ``data``. Which events are audited is the catalog's
(``EventSpec.audit``, narrowed by ``audit_only``), so a later event joins the
log by its own catalog entry. A run has one line, its ``run.finished`` or
``run.failed`` summary (user, roles, datasources, tables read, status,
source; never rows), so the log answers "who read ``payroll.salaries``"
without a line per table. Then the denials, approvals, limits and changes.

``on_event`` never blocks: it queues the line, and a thread of the
observer's own writes it, since the bus delivers a run's events under the
run's lock. The queue is bounded: when the writer cannot keep up (a stalled
disk), a line is dropped, counted in ``dropped`` and warned about once,
rather than holding a run or growing without limit. ``close`` writes what is
queued, and so does the process's exit when no one called it (an ``atexit``
hook, through a weak reference). ``engine.storage.purge()`` deletes the files
older than ``max_age_days``.
"""
from __future__ import annotations

import atexit
import logging
import queue
import threading
import weakref
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar, FrozenSet, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from nl2sql.events.catalog import CATALOG
from nl2sql_sdk.events import Event

logger = logging.getLogger("nl2sql.events")

_STOP = None
_FLUSH_TIMEOUT_S = 10
MAX_QUEUE = 10_000  # lines waiting for the writer; more are dropped and counted


def _close_at_exit(ref: "weakref.ref[AuditObserver]") -> None:
    observer = ref()
    if observer is not None:
        observer.close()


class AuditConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_age_days: int = Field(90, ge=1)


class AuditObserver:
    """Writes the audited events as JSON lines, one file per UTC day."""

    interface_version: ClassVar[int] = 1
    config_model: ClassVar[type[BaseModel]] = AuditConfig
    events: Optional[FrozenSet[str]]

    def __init__(self, directory: Path, *, max_age_days: int = 90, max_queue: int = MAX_QUEUE) -> None:
        # The catalog's audited events, read when the observer is built.
        self.events = frozenset(name for name, spec in CATALOG.items() if spec.audit)
        self.directory = Path(directory)
        self.max_age_days = max_age_days
        self.dropped = 0
        self._queue: "queue.Queue[Optional[Tuple[str, str]]]" = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._writer: Optional[threading.Thread] = None
        self._exit_hook = None
        self._closed = False

    @classmethod
    def from_config(cls, cfg: AuditConfig, *, folder: Path) -> "AuditObserver":
        return cls(Path(folder) / "audit", max_age_days=cfg.max_age_days)

    def on_event(self, event: Event) -> None:
        spec = CATALOG.get(event.name)
        if spec is None or not spec.audited(event.data):
            return
        line = (event.at.astimezone(timezone.utc).date().isoformat(), event.model_dump_json())
        with self._lock:
            if self._closed:
                return
            try:
                self._queue.put_nowait(line)
            except queue.Full:
                self.dropped += 1
                if self.dropped == 1:
                    logger.warning("The audit observer's queue is full (the disk is not keeping up): audit "
                                   "lines are dropped and counted in AuditObserver.dropped.")
                return
            if self._writer is None:
                self._writer = threading.Thread(target=self._drain, name="nl2sql-audit", daemon=True)
                self._writer.start()
                # A process that exits without closing the engine still writes what is queued.
                self._exit_hook = atexit.register(_close_at_exit, weakref.ref(self))

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            batch: List[Tuple[str, str]] = []
            stop = item is _STOP
            if not stop:
                batch.append(item)
            while True:  # whatever else is queued, in one write
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is _STOP:
                    stop = True
                else:
                    batch.append(item)
            if batch:
                try:
                    self._write(batch)
                except Exception as exc:  # fail open: a full disk never fails a run
                    logger.warning(f"The audit observer could not write {len(batch)} line(s): {exc}")
            if stop:
                return

    def _write(self, batch: List[Tuple[str, str]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        by_day: dict = {}
        for day, line in batch:
            by_day.setdefault(day, []).append(line)
        for day, lines in by_day.items():
            # Opened and closed per batch, so purge can delete a past day's file.
            with (self.directory / f"{day}.jsonl").open("a", encoding="utf-8") as out:
                out.write("\n".join(lines) + "\n")

    def purge(self, now: Optional[datetime] = None) -> int:
        """Delete the day files older than ``max_age_days``; return how many."""
        if not self.directory.is_dir():
            return 0
        oldest = ((now or datetime.now(timezone.utc)).date() - timedelta(days=self.max_age_days)).isoformat()
        deleted = 0
        for path in self.directory.glob("*.jsonl"):
            try:
                day = date.fromisoformat(path.stem).isoformat()
            except ValueError:
                continue  # not one of ours
            if day < oldest:
                path.unlink(missing_ok=True)
                deleted += 1
        return deleted

    def close(self) -> None:
        """Write what is queued and stop the writer. Calling it again does nothing."""
        with self._lock:
            if self._closed:
                return
            self._closed = True  # under the lock: no line is queued after this
            writer, hook = self._writer, self._exit_hook
        if hook is not None:
            atexit.unregister(hook)
        if writer is not None:
            try:
                self._queue.put(_STOP, timeout=_FLUSH_TIMEOUT_S)
            except queue.Full:
                pass
            writer.join(_FLUSH_TIMEOUT_S)
            if writer.is_alive():
                logger.warning("The audit observer's writer did not finish; its queued lines are not written.")
                return
        # Whatever is left (queued before a writer started, or behind the stop), written here.
        rest: List[Tuple[str, str]] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not _STOP:
                rest.append(item)
        if rest:
            try:
                self._write(rest)
            except Exception as exc:
                logger.warning(f"The audit observer could not write {len(rest)} line(s): {exc}")
