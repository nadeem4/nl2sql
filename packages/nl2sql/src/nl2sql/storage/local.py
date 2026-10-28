"""The ``local`` results store: result files in ``<storage.path>/results/``.

A key is a relative path under the folder (``<tenant>/<request>/<name>``); a
key that would lead out of it is refused. Each file is written to a
temporary file and renamed. ``max_age_days`` (default 7) is how long
``engine.storage.purge()`` keeps them.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, ClassVar, Iterable

from pydantic import BaseModel, ConfigDict, Field


class LocalConfig(BaseModel):
    """The ``local`` store's keys."""

    model_config = ConfigDict(extra="forbid")

    max_age_days: int = Field(7, ge=1)


class LocalResults:
    interface_version: ClassVar[int] = 4
    slot: ClassVar[str] = "results"
    config_model: ClassVar[type] = LocalConfig

    def __init__(self, directory: Path, max_age_days: int = 7) -> None:
        self.directory = Path(directory)
        self.max_age_days = max_age_days

    @classmethod
    def from_config(cls, cfg: LocalConfig, *, folder: Path) -> "LocalResults":
        return cls(Path(folder) / "results", cfg.max_age_days)

    def write(self, key: str, chunks: Iterable[bytes]) -> int:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        written = 0
        try:
            with os.fdopen(handle, "wb") as out:
                for chunk in chunks:
                    out.write(chunk)
                    written += len(chunk)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return written

    def open(self, key: str) -> BinaryIO:
        """The file, open for reading. Raises ``FileNotFoundError`` when there is none."""
        return open(self._path(key), "rb")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def purge(self, older_than: datetime) -> int:
        """Delete the files last written before ``older_than``, then the emptied folders; return how many files."""
        if not self.directory.is_dir():
            return 0
        cutoff = older_than.timestamp()
        deleted = 0
        for root, dirs, files in os.walk(self.directory, topdown=False):
            for name in files:
                path = Path(root) / name
                try:
                    if path.stat().st_mtime < cutoff:
                        os.remove(path)
                        deleted += 1
                except OSError:  # being read, or already gone: the next purge gets it
                    continue
            if Path(root) != self.directory:
                try:
                    os.rmdir(root)
                except OSError:  # not empty
                    pass
        return deleted

    def _path(self, key: str) -> Path:
        parts = PurePosixPath(str(key).replace("\\", "/")).parts
        if not parts or parts[0] == "/" or ":" in parts[0] or any(p in ("..", ".") for p in parts):
            raise ValueError(f"Not a results key: {key!r}. A key is a relative path such as 'tenant/run/name'.")
        return self.directory.joinpath(*parts)
