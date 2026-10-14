"""The ``local`` results store: result files in ``<storage.path>/results/``.

For now a thin wrapper over ``nl2sql.execution.artifacts.ArtifactStore``,
which it builds. It becomes the ``ResultStore`` in Phase 2's S4
(docs/architecture/phase2.md).
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from nl2sql.storage import NoKeys


class LocalResults:
    interface_version: ClassVar[int] = 1
    slot: ClassVar[str] = "results"
    config_model: ClassVar[type] = NoKeys

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def from_config(cls, cfg: NoKeys, *, folder: Path) -> "LocalResults":
        return cls(Path(folder) / "results")

    def artifact_store(self):
        """Today's ``ArtifactStore`` on this folder."""
        from nl2sql.execution.artifacts import ArtifactStore, ArtifactStoreConfig

        return ArtifactStore(ArtifactStoreConfig(base_uri=str(self.directory)))
