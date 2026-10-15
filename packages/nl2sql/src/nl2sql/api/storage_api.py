"""``engine.storage``: where the engine keeps its data, and retention."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from nl2sql.observers.audit import AuditObserver
from nl2sql.runtime import Runtime

# The slots retention applies to. Feedback and schema snapshots have no age limit.
_RETAINED = ("traces", "results")


class StorageAPI:
    """``engine.storage``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def info(self) -> Dict[str, Any]:
        """Each slot's store and the current index namespace.

        Returns ``{"index": {...}, "state": {...}, "traces": {...},
        "results": {...}, "namespace": "nl2sql__<model>__<dimension>"}``. Each
        slot has ``type`` (the store's name, or its class name for a store
        object), ``location`` (its folder or file, or None for a store that
        does not say) and, for a store with retention, ``max_age_days``.
        """
        from nl2sql.config.models import STORE_SLOTS, StoreConfig

        storage = self._ctx.config.storage
        info: Dict[str, Any] = {}
        for slot in STORE_SLOTS:
            configured, store = getattr(storage, slot), getattr(self._ctx, slot)
            location = getattr(store, "directory", None) or getattr(store, "path", None)
            entry = {"type": configured.type if isinstance(configured, StoreConfig) else type(store).__name__,
                     "location": str(location) if location is not None else None}
            if slot in _RETAINED and getattr(store, "max_age_days", None) is not None:
                entry["max_age_days"] = store.max_age_days
            info[slot] = entry
        info["namespace"] = self._ctx.namespace
        return info

    def purge(self) -> Dict[str, int]:
        """Delete the traces, results and audit files older than their ``max_age_days``; return the counts.

        ``{"traces": 3, "results": 12, "audit": 1}``: traces deleted, result
        files deleted (a run has its manifest and one file per saved result),
        and the audit observer's day files deleted. A store with no
        ``max_age_days`` keeps everything and is left out, and so is
        ``audit`` when the engine runs no ``audit`` observer.
        ``nl2sql storage purge`` calls this, and ``nl2sql-api`` calls it at
        startup and once a day. It publishes ``retention.purged`` with the
        counts on the engine's bus, which the audit observer writes.
        """
        now = datetime.now(timezone.utc)
        counts: Dict[str, int] = {}
        for slot in _RETAINED:
            store = getattr(self._ctx, slot)
            days = getattr(store, "max_age_days", None)
            if days is not None:
                counts[slot] = store.purge(now - timedelta(days=days))
        audits = [o for o in getattr(self._ctx, "observers", ()) if isinstance(o, AuditObserver)]
        if audits:
            counts["audit"] = sum(audit.purge(now) for audit in audits)
        self._ctx.bus.publish("retention.purged", {"counts": counts}, tenant_id=self._ctx.tenant_id)
        return counts
