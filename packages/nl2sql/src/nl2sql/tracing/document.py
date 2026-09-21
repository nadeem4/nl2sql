"""The run-trace file: JSON conversion, redaction, capping, naming and lookup.

A trace is one self-contained JSON document per run. This module owns what
happens to it between the recorder and the disk:

* :func:`jsonable` turns whatever a node read or wrote into plain JSON.
* :class:`Redactor` removes secrets. It is applied to the whole document, last,
  so nothing a later step adds can bring a secret back.
* :func:`cap` bounds the size of node inputs and outputs. Row data keeps
  ``TRACE_SAMPLE_ROWS`` rows; other lists keep ``max_list_items`` items; long
  strings keep ``TRACE_MAX_FIELD_CHARS`` characters. Each cut leaves a marker
  saying how much was dropped.
* :func:`write_trace` / :func:`find_trace` name files
  ``<UTC timestamp>_<trace_id>.json`` (sortable) and look them up by id without
  ever leaving the traces directory.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import enum
import json
import pathlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Optional

TRACE_FORMAT_VERSION = 1

TRUNCATED = "[truncated]"
REDACTED = "[REDACTED]"

# A trace id is a UUID in practice; anything else that is not a plain token is
# refused before it gets near a path.
_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Keys whose string values are secrets whatever they look like.
_SECRET_KEY = re.compile(
    r"(^|_)(api_?key|apikey|password|passwd|pwd|secret|client_secret|authorization|"
    r"access_?key|account_?key|connection_string|credentials?|bearer|(access|refresh|auth|id)_?token)$",
    re.IGNORECASE,
)

# Secret-shaped substrings, redacted even when the value was never registered.
_PATTERNS = (
    # OpenAI / OpenRouter / Anthropic style keys.
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{8,}"), REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"), f"Bearer {REDACTED}"),
    # scheme://user:password@host -> scheme://user:[REDACTED]@host
    (re.compile(r"(://[^/\s:@]+):[^@\s/]+@"), rf"\1:{REDACTED}@"),
    # key=value pairs in connection strings and headers.
    (re.compile(r"(?i)\b(password|pwd|passwd|secret|api[_-]?key|access[_-]?key|accountkey|"
                r"sharedaccesskey|token)(\s*[=:]\s*)[^;\s,\"'&]+"), rf"\1\2{REDACTED}"),
)


@dataclass(frozen=True)
class Limits:
    sample_rows: int = 50
    max_field_chars: int = 20000
    max_list_items: int = 200


def jsonable(value: Any, _depth: int = 0) -> Any:
    """Plain JSON for anything a node reads or writes."""
    if _depth > 64:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, enum.Enum):
        return jsonable(value.value, _depth + 1)
    if isinstance(value, dict):
        return {str(k): jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v, _depth + 1) for v in value]
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, pathlib.PurePath)):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, type):
        return value.__name__
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return jsonable(dump(mode="json"), _depth + 1)
        except Exception:
            try:
                return jsonable(dump(), _depth + 1)
            except Exception:
                return str(value)
    if dataclasses.is_dataclass(value):
        return jsonable(dataclasses.asdict(value), _depth + 1)
    get_secret = getattr(value, "get_secret_value", None)
    if callable(get_secret):
        return REDACTED
    return str(value)


class Redactor:
    """Replaces secret values and secret-shaped text with ``[REDACTED]``."""

    def __init__(self, secrets: Iterable[Optional[str]] = ()):
        # Longest first, so a secret containing another is removed whole. Very
        # short values would redact ordinary words, so they are left to the patterns.
        self._secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def redact_text(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        for pattern, replacement in _PATTERNS:
            text = pattern.sub(replacement, text)
        return text

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if isinstance(item, str) and item and _SECRET_KEY.search(str(key)):
                    out[key] = REDACTED
                else:
                    out[key] = self.redact(item)
            return out
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        return value


_ROW_KEYS = {"rows", "terminal_results"}


def cap(value: Any, limits: Limits, _rows: bool = False, _key: Optional[str] = None) -> Any:
    """Bounds strings and lists, leaving a marker wherever something was cut."""
    if isinstance(value, str):
        if len(value) > limits.max_field_chars:
            dropped = len(value) - limits.max_field_chars
            return value[: limits.max_field_chars] + f"... {TRUNCATED} {dropped} more characters"
        return value
    if isinstance(value, dict):
        return {k: cap(v, limits, _rows=(k in _ROW_KEYS) or (_rows and _key == "terminal_results"), _key=k)
                for k, v in value.items()}
    if isinstance(value, list):
        limit = limits.sample_rows if _rows else limits.max_list_items
        kept = [cap(v, limits) for v in value[:limit]]
        if len(value) > limit:
            what = "rows" if _rows else "items"
            kept.append(f"{TRUNCATED} {len(value) - limit} more {what} (kept {limit})")
        return kept
    return value


def should_write(mode: str, failed: bool) -> bool:
    if mode == "always":
        return True
    if mode == "on_failure":
        return bool(failed)
    return False


def validate_trace_id(trace_id: str) -> str:
    """Returns ``trace_id`` if it is a plain token, else raises ``ValueError``."""
    if not isinstance(trace_id, str) or not _TRACE_ID.match(trace_id):
        raise ValueError(f"Not a valid trace id: {trace_id!r}")
    return trace_id


def trace_filename(trace_id: str, now: Optional[_dt.datetime] = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%S%f')}Z_{validate_trace_id(trace_id)}.json"


def write_trace(doc: dict, directory: pathlib.Path) -> pathlib.Path:
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / trace_filename(doc["trace_id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def find_trace(trace_id: str, directory: pathlib.Path) -> Optional[pathlib.Path]:
    """The newest trace file for ``trace_id`` directly inside ``directory``, or None.

    The id is validated first, and every candidate must resolve to a file whose
    parent *is* the traces directory, so neither ``..`` nor a link can lead out.
    """
    validate_trace_id(trace_id)
    directory = pathlib.Path(directory)
    if not directory.is_dir():
        return None
    root = directory.resolve()
    matches = sorted(p for p in directory.glob(f"*_{trace_id}.json") if p.is_file())
    for path in reversed(matches):
        if path.resolve().parent == root:
            return path
    return None


def load_trace(path: pathlib.Path) -> dict:
    doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    version = doc.get("trace_format_version")
    if version != TRACE_FORMAT_VERSION:
        raise ValueError(
            f"{path} is trace format version {version!r}; this engine reads version {TRACE_FORMAT_VERSION}."
        )
    return doc
