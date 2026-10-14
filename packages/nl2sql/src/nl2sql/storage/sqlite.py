"""The ``sqlite`` state store: ``<storage.path>/state.db``.

For now a thin wrapper over ``nl2sql.schema.sqlite_store.SqliteSchemaStore``,
which it builds. It becomes the ``StateStore`` (snapshots, plan cache,
feedback, migrations, the lock) in Phase 2's S3 (docs/architecture/phase2.md).
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from nl2sql.storage import NoKeys


class SqliteState:
    interface_version: ClassVar[int] = 1
    slot: ClassVar[str] = "state"
    config_model: ClassVar[type] = NoKeys

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def from_config(cls, cfg: NoKeys, *, folder: Path) -> "SqliteState":
        return cls(Path(folder) / "state.db")

    def schema_store(self, max_versions: int):
        """Today's ``SqliteSchemaStore`` on this file. Creates the file."""
        from nl2sql.schema.sqlite_store import SqliteSchemaStore

        return SqliteSchemaStore(path=self.path, max_versions=max_versions)
