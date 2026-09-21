"""A small version stamp in each demo folder.

Demo folders are never upgraded in place: a folder scaffolded by an older
engine keeps that engine's defaults (an older model, missing settings such as
``TRACE_MODE``). The stamp records which engine wrote the folder, so a later
start can say so and suggest a fresh ``--dir``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Optional

STAMP_FILE = "nl2sql-demo.json"


def engine_version() -> str:
    try:
        return version("nl2sql-engine")
    except PackageNotFoundError:
        return "0+unknown"


def write_stamp(directory: Path) -> None:
    body = {
        "engine_version": engine_version(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (Path(directory) / STAMP_FILE).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def read_stamp(directory: Path) -> Optional[str]:
    """The engine version that scaffolded ``directory``, or None when unstamped."""
    path = Path(directory) / STAMP_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("engine_version") or None
    except (OSError, ValueError, AttributeError):
        return None


def _older(created_by: str, current: str) -> bool:
    try:
        from packaging.version import Version

        return Version(created_by) < Version(current)
    except Exception:
        return created_by != current


def outdated_warning(directory: Path) -> Optional[str]:
    """A sentence for a folder written by an older engine, else None."""
    created_by = read_stamp(directory)
    current = engine_version()
    if created_by is None:
        origin = "an older version of nl2sql-engine (before demo folders were stamped)"
    elif _older(created_by, current):
        origin = f"nl2sql-engine {created_by}"
    else:
        return None
    return (
        f"This demo folder was created by {origin}; you are running {current}. "
        "Demo folders are not upgraded in place, so it may be missing newer defaults. "
        f"To start fresh, run: nl2sql demo --dir {Path(directory).name}-new"
    )
