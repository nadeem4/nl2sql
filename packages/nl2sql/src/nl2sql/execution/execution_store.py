from __future__ import annotations

from threading import Lock
from typing import Dict, Optional

from .contracts import ArtifactRef


class ExecutionStore:
    """In-memory store for artifact references keyed by node id."""

    def __init__(self) -> None:
        self._artifacts: Dict[str, ArtifactRef] = {}
        self._lock = Lock()

    def put(self, node_id: str, artifact: ArtifactRef) -> None:
        with self._lock:
            self._artifacts[node_id] = artifact

    def get(self, node_id: str) -> Optional[ArtifactRef]:
        return self._artifacts.get(node_id)

    def snapshot(self) -> Dict[str, ArtifactRef]:
        with self._lock:
            return dict(self._artifacts)
