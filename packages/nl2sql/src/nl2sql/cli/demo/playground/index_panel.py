"""The playground's index panel: index health on load, and a Rebuild action.

The owner's rule is that the playground always offers an index action, so an
empty or stale index is repaired from the page rather than from a terminal.
Rebuild re-reads the datasource's schema into a new snapshot and rebuilds its
vector entries beside the current ones (``nl2sql.indexing.rebuild``); the
current entries keep answering questions until the new ones are complete.

Rebuild borrows every guardrail from the settings panel:

* **Local only.** The same ``SettingsPanel.guard``: refused on a host other
  machines can reach unless ``--allow-settings`` is given, and only from the
  playground page itself.
* **Questions in flight finish first.** The switch to the new entries is held
  under the settings panel's ``RunGate``.
* **Enrichment is opt-in.** LLM-written descriptions spend tokens on the
  owner's key, so they are off unless asked for, and unavailable without a key.

The rebuild runs on a background thread; the page polls ``GET /api/index`` for
its steps (the first run downloads a 79 MB embedding model).
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from nl2sql.cli.demo.stamp import engine_version, outdated_warning, read_stamp
from nl2sql.common.logger import get_logger
from nl2sql.indexing.health import inspect_vector_store
from nl2sql.indexing.rebuild import rebuild_index

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IndexPanel:
    """Reports index health and runs one guarded rebuild at a time."""

    def __init__(self, engine, settings_panel, datasource_id: str,
                 project_dir: Optional[Path] = None) -> None:
        self.engine = engine
        self.settings = settings_panel
        self.datasource_id = datasource_id
        self.project_dir = Path(project_dir) if project_dir is not None else None
        self._lock = threading.Lock()
        self._job: Dict[str, Any] = {
            "state": "idle", "steps": [], "error": None, "enrich": False,
            "started_at": None, "finished_at": None,
        }

    # --- reading ---------------------------------------------------------------

    def _context(self):
        return getattr(self.engine, "context", None)

    def health(self) -> Dict[str, Any]:
        ctx = self._context()
        store = getattr(ctx, "vector_store", None)
        if store is None:
            return {"status": "missing", "total": 0, "counts": {}, "built_at": None,
                    "embedding_model": None, "datasources": [],
                    "problems": ["This playground has no vector index configured."]}
        registry = getattr(ctx, "ds_registry", None)
        ids: List[str] = [a.datasource_id for a in registry.list_adapters()] if registry else []
        return inspect_vector_store(store, getattr(ctx, "schema_store", None), ids).to_dict()

    def _folder(self) -> Optional[Dict[str, Any]]:
        if self.project_dir is None:
            return None
        return {
            "created_by": read_stamp(self.project_dir),
            "engine": engine_version(),
            "warning": outdated_warning(self.project_dir),
        }

    def read(self) -> Dict[str, Any]:
        with self._lock:
            job = {**self._job, "steps": list(self._job["steps"])}
        live = self.settings.mode == "live"
        return {
            "datasource_id": self.datasource_id,
            "health": self.health(),
            "rebuild": {
                "available": self.settings.available,
                "reason": self.settings.reason,
                "enrich_available": live,
                "enrich_reason": None if live else (
                    "Descriptions are written by the LLM, so they need an API key. Add one in Settings."
                ),
            },
            "job": job,
            "folder": self._folder(),
        }

    # --- rebuilding ------------------------------------------------------------

    def start(self, enrich: bool) -> None:
        """Starts a rebuild in the background; refuses a second one."""
        if enrich and self.settings.mode != "live":
            raise HTTPException(
                status_code=400,
                detail="Descriptions need an API key. Add one in Settings, or rebuild without them.",
            )
        with self._lock:
            if self._job["state"] == "running":
                raise HTTPException(status_code=409, detail="A rebuild is already running.")
            self._job = {"state": "running", "steps": [], "error": None, "enrich": enrich,
                         "started_at": _now(), "finished_at": None}
        threading.Thread(target=self._run, args=(enrich,), name="index-rebuild", daemon=True).start()

    def _step(self, message: str) -> None:
        with self._lock:
            self._job["steps"].append(message)

    def _finish(self, state: str, error: Optional[str] = None) -> None:
        with self._lock:
            self._job.update(state=state, error=error, finished_at=_now())

    def _run(self, enrich: bool) -> None:
        try:
            result = rebuild_index(
                self._context(),
                enrich=enrich,
                datasource_ids=[self.datasource_id],
                on_progress=self._step,
                switch_guard=self.settings.gate.change,
            )
        except Exception as exc:
            logger.error("Index rebuild failed: %s", exc)
            self._finish("failed", f"{exc} The previous index is unchanged.")
            return
        if result.ok:
            self._step("Done")
            self._finish("done")
        else:
            reasons = "; ".join(f"{e['datasource_id']}: {e['error']}" for e in result.errors) or "nothing was indexed"
            self._finish("failed", f"The rebuild failed ({reasons}). The previous index is unchanged.")
