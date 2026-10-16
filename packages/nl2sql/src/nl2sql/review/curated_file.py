"""The curated section's own file: where it is, its canonical form and its writer (docs/architecture/wave_b.md, D42).

``write_curated`` is engine-internal: only the review queue's accept calls it,
under its lock, after its admin check (``test_only_accept_writes_a_persons_file``).
The canonical form keeps the models' key order, sorts mappings by name, keeps
lists in their own order and writes SQL and other multi-line text as literal
blocks, under a header saying the file is rewritten on accept. Comments are not
kept; a query's ``description`` holds remarks.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

import yaml

if TYPE_CHECKING:
    from nl2sql.config import Config, Curated

HEADER = (
    "# The curated answers: verified queries per datasource.\n"
    "# `nl2sql review accept` rewrites this file in canonical form; comments are not kept.\n"
    "# Write remarks in a query's `description:`.\n"
)


def curated_path(config: "Config") -> Optional[Path]:
    """The file the ``curated`` section was read from; None when it is inline, absent, or the config was built in
    code."""
    if config.curated is None or config.folder is None:
        return None
    file, _ = config.locate("curated")
    if file == "nl2sql.yaml":
        return None
    return Path(config.folder) / file


def file_sha256(path: Path) -> Optional[str]:
    """The SHA-256 of the file's bytes, or None when there is no file."""
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


STALE = "stale"  # the file no longer holds the section the config loaded: accept refuses with FILE_CHANGED


def loaded_sha256(config: "Config") -> Optional[str]:
    """The hash of the curated section's own file, when it still holds the section ``config`` loaded.

    The file is read once: its bytes are hashed and parsed. When the parse is
    not ``config.curated`` (a person edited the file after the config was
    loaded), the hash is ``STALE``, which no file matches, so accept refuses
    rather than write over the edit. None when the section has no file.
    """
    from pydantic import ValidationError

    from nl2sql.config import Curated

    path = curated_path(config)
    if path is None:
        return None
    try:
        data = Path(path).read_bytes()
        loaded = Curated.model_validate(yaml.safe_load(data) or {})
    except (OSError, yaml.YAMLError, ValidationError):
        return STALE
    return hashlib.sha256(data).hexdigest() if loaded == config.curated else STALE


class _Dumper(yaml.SafeDumper):
    """Multi-line text (the SQL) as a literal block, so the file reads as written."""


_Dumper.add_representer(
    str,
    lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None),
)


def _sorted(data: Dict[str, Any]) -> Dict[str, Any]:
    datasources = {}
    for ds_id in sorted(data.get("datasources", {})):
        block = dict(data["datasources"][ds_id])
        block["verified_queries"] = [
            {**query, "params": {name: query["params"][name] for name in sorted(query["params"])}}
            if "params" in query else query
            for query in block.get("verified_queries", [])]
        if not block["verified_queries"]:
            del block["verified_queries"]
        datasources[ds_id] = block
    return {"datasources": datasources}


def canonical_curated(curated: "Curated") -> str:
    """The canonical text of the ``curated`` section's body. ``canonical_curated`` of its own parse is itself."""
    data = _sorted(curated.model_dump(mode="json", exclude_defaults=True))
    return HEADER + yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def write_curated(path: Path, curated: "Curated", expected_sha256: Optional[str]) -> str:
    """Rewrite ``path`` with ``curated`` in canonical form; return the new file's SHA-256.

    The real path is resolved first, so a symlinked file is written through
    and the link kept; the text goes to a temporary file beside it, which is
    renamed over it.

    Raises:
        ReviewError: ``FILE_CHANGED`` when the file's hash is no longer
            ``expected_sha256`` (the hash when the engine loaded it);
            ``NOT_WRITABLE`` when the file cannot be replaced. Nothing is
            written either way.
    """
    from nl2sql.review.models import ReviewError, ReviewErrorCode

    real = Path(os.path.realpath(path))
    if file_sha256(real) != expected_sha256:
        raise ReviewError(ReviewErrorCode.FILE_CHANGED,
                          f"{Path(path).as_posix()} changed on disk since the engine loaded it.",
                          fix="Reload the engine (restart it, or build a new NL2SQL) and accept again.")
    text = canonical_curated(curated).encode("utf-8")
    handle, temporary = tempfile.mkstemp(prefix=f".{real.name}.", suffix=".tmp", dir=real.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        if real.exists():
            shutil.copymode(real, temporary)
        os.replace(temporary, real)
    except PermissionError as e:
        Path(temporary).unlink(missing_ok=True)
        raise ReviewError(ReviewErrorCode.NOT_WRITABLE,
                          f"{Path(path).as_posix()} cannot be replaced: {e.strerror or e}.",
                          fix="Close the program that holds the file open, then accept again.") from None
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return hashlib.sha256(text).hexdigest()
