"""The ``feedback`` table, kept in the SQLite schema store (``schema_store.db``).

One row per run (by trace id): a later rating of the same run replaces the
earlier one. Nothing else in the file is read or changed, so ``nl2sql feedback
clear`` empties this table only, and ``nl2sql cache clear`` never touches it.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

RATINGS = ("up", "down")
NOTE_MAX_CHARS = 280

_JSON_COLUMNS = ("sql", "error_codes", "models")
_COLUMNS = ("trace_id", "created_at", "rating", "note", "question", "role", "status", "sql",
            "error_codes", "retries", "validator_failures", "plan_cache_hits", "sub_queries",
            "models", "engine_version")

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    trace_id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
    note TEXT,
    question TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    sql TEXT NOT NULL,
    error_codes TEXT NOT NULL,
    retries INTEGER NOT NULL DEFAULT 0,
    validator_failures INTEGER NOT NULL DEFAULT 0,
    plan_cache_hits INTEGER NOT NULL DEFAULT 0,
    sub_queries INTEGER NOT NULL DEFAULT 0,
    models TEXT NOT NULL,
    engine_version TEXT NOT NULL
);
"""


class FeedbackStore:
    """Reads and writes the feedback table of one ``schema_store.db``."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self._path), check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL;")
        self._connection.execute(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def save(self, record: Mapping[str, Any], rating: str, note: Optional[str]) -> Dict[str, Any]:
        """Stores ``record`` with its rating, replacing any earlier rating of the same run."""
        if rating not in RATINGS:
            raise ValueError(f"A rating is 'up' or 'down', not {rating!r}.")
        note = (note or "").strip()[:NOTE_MAX_CHARS] or None
        row = {
            "trace_id": record["trace_id"],
            "created_at": int(time.time()),
            "rating": rating,
            "note": note,
            "question": record.get("question") or "",
            "role": record.get("role") or "",
            "status": record.get("status") or "",
            "sql": json.dumps(list(record.get("sql") or [])),
            "error_codes": json.dumps(list(record.get("error_codes") or [])),
            "retries": int(record.get("retries") or 0),
            "validator_failures": int(record.get("validator_failures") or 0),
            "plan_cache_hits": int(record.get("plan_cache_hits") or 0),
            "sub_queries": int(record.get("sub_queries") or 0),
            "models": json.dumps(dict(record.get("models") or {})),
            "engine_version": record.get("engine_version") or "",
        }
        with self._connection:
            self._connection.execute(
                f"INSERT OR REPLACE INTO feedback ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)});",
                [row[c] for c in _COLUMNS],
            )
        return self._decode(row)

    def list(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Every rating, newest first."""
        query = f"SELECT {', '.join(_COLUMNS)} FROM feedback ORDER BY created_at DESC, rowid DESC"
        params: tuple = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (int(limit),)
        rows = self._connection.execute(query, params).fetchall()
        return [self._decode(dict(zip(_COLUMNS, r))) for r in rows]

    def counts(self) -> Dict[str, int]:
        found = dict(self._connection.execute("SELECT rating, COUNT(*) FROM feedback GROUP BY rating").fetchall())
        return {r: int(found.get(r, 0)) for r in RATINGS}

    def clear(self) -> int:
        with self._connection:
            return self._connection.execute("DELETE FROM feedback;").rowcount

    @staticmethod
    def _decode(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        for column in _JSON_COLUMNS:
            if isinstance(out.get(column), str):
                out[column] = json.loads(out[column])
        return out
