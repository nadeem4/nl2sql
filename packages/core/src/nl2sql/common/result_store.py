from __future__ import annotations

import hashlib
import json
from threading import Lock
from typing import Any, Dict, Optional

from nl2sql_adapter_sdk.contracts import ResultFrame


class ResultStore:
    """In-memory result store with deterministic content hashes."""

    def __init__(self) -> None:
        self._results: Dict[str, ResultFrame] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()

    def _hash(self, payload: Dict[str, Any]) -> str:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def put(self, frame: ResultFrame, metadata: Optional[Dict[str, Any]] = None) -> str:
        metadata = metadata or {}
        payload = {
            "rows": frame.to_row_dicts(),
            "columns": [c.name for c in frame.columns],
            "metadata": metadata,
        }
        result_id = self._hash(payload)
        with self._lock:
            if result_id not in self._results:
                self._results[result_id] = frame
                self._metadata[result_id] = metadata
        return result_id

    def get(self, result_id: str) -> ResultFrame:
        return self._results[result_id]

    def get_metadata(self, result_id: str) -> Dict[str, Any]:
        return self._metadata.get(result_id, {})
