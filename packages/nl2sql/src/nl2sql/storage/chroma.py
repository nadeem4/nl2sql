"""The ``chroma`` index store: the Chroma vector index in ``<storage.path>/index/``.

For now a thin wrapper over ``nl2sql.indexing.vector_store.VectorStore``,
which it builds. It moves onto the ``IndexStore`` interface, on Chroma's
public client API, in Phase 2's S2 (docs/architecture/phase2.md).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from nl2sql.storage import NoKeys

# The index's collection: an internal constant, one per index folder.
COLLECTION = "nl2sql_store"


class ChromaIndex:
    interface_version: ClassVar[int] = 1
    slot: ClassVar[str] = "index"
    config_model: ClassVar[type] = NoKeys

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def from_config(cls, cfg: NoKeys, *, folder: Path) -> "ChromaIndex":
        return cls(Path(folder) / "index")

    def vector_store(self, embeddings: Any):
        """Today's ``VectorStore`` on this folder, embedding with ``embeddings``. Creates the folder."""
        from nl2sql.indexing.vector_store import VectorStore

        self.directory.mkdir(parents=True, exist_ok=True)
        return VectorStore(collection_name=COLLECTION, persist_directory=str(self.directory), embeddings=embeddings)
