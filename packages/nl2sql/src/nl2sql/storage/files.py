"""The ``files`` trace store: one JSON document per run in ``<storage.path>/traces/``.

For now it holds the folder the trace writer writes to. It becomes the
``TraceStore`` in Phase 2's S4 (docs/architecture/phase2.md).
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from nl2sql.storage import NoKeys


class FilesTraces:
    interface_version: ClassVar[int] = 1
    slot: ClassVar[str] = "traces"
    config_model: ClassVar[type] = NoKeys

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def from_config(cls, cfg: NoKeys, *, folder: Path) -> "FilesTraces":
        return cls(Path(folder) / "traces")
