from __future__ import annotations

from pathlib import Path
from typing import Optional

from .in_memory_store import InMemorySchemaStore
from .protocol import SchemaStore
from .sqlite_store import SqliteSchemaStore


SCHEMA_STORE_BACKENDS = {
    "memory": InMemorySchemaStore,
    "sqlite": SqliteSchemaStore,
}


def build_schema_store(
    backend: str,
    max_versions: int,
    path: Optional[Path] = None,
) -> SchemaStore:
    backend_key = (backend or "sqlite").lower()
    if backend_key == "memory":
        return InMemorySchemaStore(max_versions=max_versions)
    if backend_key == "sqlite":
        if path is None:
            raise ValueError("schema_store_path is required for sqlite backend.")
        return SqliteSchemaStore(path=path, max_versions=max_versions)
    raise ValueError(f"Unsupported schema store backend: {backend}")
