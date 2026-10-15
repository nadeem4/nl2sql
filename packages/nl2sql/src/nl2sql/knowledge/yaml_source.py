"""The built-in ``yaml`` knowledge source, and the engine's glossary writer (docs/architecture/wave_b.md, section 3).

``YamlKnowledgeSource`` reads one ``glossary.yaml`` (``path``, relative to the
folder of ``nl2sql.yaml``) with ``yaml.safe_load`` and validates it with the
SDK's ``Glossary``. It is installed under the ``nl2sql.knowledge_sources``
entry point group as ``yaml``.

``write_glossary`` is engine-internal, not part of the SDK contract (D36):
only the review queue's accept calls it (D42). It writes the canonical form
(``canonical_yaml``), refuses when the file changed since it was read, writes
through a symlink, and replaces the file atomically.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from nl2sql_sdk.knowledge import ITEM_MAX_CHARS, Glossary

HEADER = """\
# glossary.yaml: what people know about the data, read by the yaml knowledge source.
# The engine rewrites this file in a canonical form when a reviewer accepts a
# proposal: comments are not kept, so write remarks in a `notes:` field.
# See docs/configuration/knowledge.md.
"""


class YamlSourceConfig(BaseModel):
    """The ``yaml`` source's keys: ``path``, the glossary file, relative to the folder of ``nl2sql.yaml``."""

    model_config = ConfigDict(extra="forbid")

    path: Path


class YamlKnowledgeSource:
    """Reads one ``glossary.yaml``. ``sha256`` is the hash of the bytes the last ``load`` read."""

    interface_version: ClassVar[int] = 1
    config_model: ClassVar[type[BaseModel]] = YamlSourceConfig
    type: ClassVar[str] = "yaml"

    def __init__(self, path: Path, *, item_max_chars: int = ITEM_MAX_CHARS):
        self.path = Path(path)
        self.item_max_chars = item_max_chars
        self.sha256: Optional[str] = None

    @classmethod
    def from_config(cls, cfg: BaseModel, *, folder: Path) -> "YamlKnowledgeSource":
        path = Path(cfg.path)
        return cls(path if path.is_absolute() else Path(folder) / path)

    def load(self) -> Glossary:
        """The glossary in the file, now.

        Raises:
            ValueError: the file does not exist, is not YAML, or is not a valid
                glossary; the message names the file and each wrong item.
        """
        if not self.path.is_file():
            raise ValueError(f"The glossary file {self.path.as_posix()} does not exist. `nl2sql init` writes "
                             f"one; or fix the source's `path`.")
        data = self.path.read_bytes()
        try:
            document = yaml.safe_load(data)
        except yaml.YAMLError as e:
            raise ValueError(f"{self.path.as_posix()} is not valid YAML: {e}") from None
        glossary = parse_glossary(document, item_max_chars=self.item_max_chars, source=self.path.as_posix())
        self.sha256 = hashlib.sha256(data).hexdigest()
        return glossary


def parse_glossary(document: Any, *, item_max_chars: int, source: str) -> Glossary:
    """``document`` as a ``Glossary``, every text capped at ``item_max_chars``.

    Raises:
        ValueError: naming ``source`` and each item that is wrong.
    """
    if not isinstance(document, dict):
        raise ValueError(f"{source} is not a glossary: it holds no mapping (write `version: 1`).")
    try:
        return Glossary.model_validate(document, context={"item_max_chars": item_max_chars})
    except ValidationError as e:
        reasons = "; ".join(f"{'.'.join(str(p) for p in error['loc']) or 'the file'}: {error['msg']}"
                            for error in e.errors())
        raise ValueError(f"{source} is not a valid glossary: {reasons}.") from None


def file_sha256(path: Path) -> Optional[str]:
    """The SHA-256 of the file's bytes, or None when it does not exist."""
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class _Dumper(yaml.SafeDumper):
    """Long or multi-line text as a literal block, so a file reads as written."""


_Dumper.add_representer(
    str,
    lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None),
)


def _sorted_mappings(data: Dict[str, Any]) -> Dict[str, Any]:
    """The glossary's dicts keyed by a name sorted by that name; model fields keep the models' order."""
    out = dict(data)
    out["datasources"] = {}
    for ds_id in sorted(data.get("datasources", {})):
        block = dict(data["datasources"][ds_id])
        for key in ("tables", "columns"):
            if key in block:
                block[key] = {name: _sorted_values(block[key][name]) for name in sorted(block[key])}
        out["datasources"][ds_id] = block
    return out


def _sorted_values(item: Dict[str, Any]) -> Dict[str, Any]:
    if "values" in item:
        item = {**item, "values": {code: item["values"][code] for code in sorted(item["values"])}}
    return item


def canonical_yaml(glossary: Glossary) -> str:
    """The canonical text of ``glossary``: the header, then the models' keys in their order, unset ones left
    out, mappings sorted by name, lists in their own order. ``canonical_yaml`` of its own parse is itself."""
    data = glossary.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    data = _sorted_mappings({"version": glossary.version, **data})
    return HEADER + yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def write_glossary(path: Path, glossary: Glossary, expected_sha256: Optional[str]) -> str:
    """Rewrite ``path`` with ``glossary`` in canonical form; return the new file's SHA-256.

    Engine-internal: only the review queue's accept calls it, under its lock
    (D42). The real path is resolved first, so a symlinked file is written
    through and the link kept; the text goes to a temporary file beside it,
    which is renamed over it.

    Raises:
        ReviewError: ``FILE_CHANGED`` when the file's hash is no longer
            ``expected_sha256`` (the hash when it was loaded). Nothing is written.
    """
    from nl2sql.review.models import ReviewError, ReviewErrorCode

    real = Path(os.path.realpath(path))
    if file_sha256(real) != expected_sha256:
        raise ReviewError(ReviewErrorCode.FILE_CHANGED,
                          f"{Path(path).as_posix()} changed on disk since the engine loaded it.",
                          fix="Reload the engine's knowledge (engine.knowledge.reload) and accept again.")
    text = canonical_yaml(glossary).encode("utf-8")
    handle, temporary = tempfile.mkstemp(prefix=f".{real.name}.", suffix=".tmp", dir=real.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(text)
        os.replace(temporary, real)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return hashlib.sha256(text).hexdigest()
