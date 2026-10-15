"""The ``sqlite`` state store: ``<storage.path>/state.db``.

Schema snapshots and the diffs between them, the plan cache, answer feedback,
the migrations that build them, and a lock (docs/architecture/phase2.md,
section 4; the diffs, docs/architecture/wave_a.md, section 10).

* **Snapshots are immutable.** A row is keyed by ``(datasource_id,
  schema_version)`` and never updated: new content is a new version, and
  storing content already held returns its version. A trigger refuses any
  ``UPDATE``. Old versions go only through :meth:`SqliteState.keep_snapshots`.
* **Migrations.** ``nl2sql_meta`` records each numbered migration applied.
  Migration 1 creates 0.5's three tables if they are missing, so a 0.5
  ``state.db`` is adopted as it is. They run in one ``BEGIN IMMEDIATE``
  transaction that re-reads ``nl2sql_meta`` first, so two stores opening a
  new file at once apply each migration exactly once.
* **The lock** is a lease: a ``locks`` row taken in a ``BEGIN IMMEDIATE``
  transaction, with an expiry so a holder that died cannot block forever.

Safe to share between threads: every use of the one connection holds a lock,
since sqlite3 does not serialise a shared connection's statements itself.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Iterator, List, Optional, Set, Tuple, Union
from uuid import uuid4

from nl2sql.common.logger import get_logger
from nl2sql.schema.fingerprint import generate_schema_fingerprint
from nl2sql.storage import NoKeys
from nl2sql_sdk.schema import SchemaContract, SchemaDiff, SchemaMetadata, SchemaSnapshot

logger = get_logger(__name__)


class LockHeld(RuntimeError):
    """Another holder has the lease on this name, and it has not expired."""


# -- migrations --------------------------------------------------------------------

def _script(connection: sqlite3.Connection, script: str) -> None:
    """Runs ``script`` one statement at a time, inside the caller's transaction.

    ``executescript`` would commit that transaction first.
    """
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError(f"An incomplete statement in a migration: {statement.strip()!r}")


def _adopt_0_5(connection: sqlite3.Connection) -> None:
    """0.5's three tables, as 0.5 created them; a 0.5 file already has them."""
    _script(connection, """
        CREATE TABLE IF NOT EXISTS schema_snapshots (
            datasource_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            contract_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (datasource_id, schema_version)
        );
        CREATE INDEX IF NOT EXISTS idx_schema_snapshots_fingerprint
            ON schema_snapshots (datasource_id, fingerprint);
        CREATE INDEX IF NOT EXISTS idx_schema_snapshots_created_at
            ON schema_snapshots (datasource_id, created_at);
        CREATE TABLE IF NOT EXISTS plan_cache (
            question_key TEXT NOT NULL,
            datasource_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (question_key, datasource_id, schema_version)
        );
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
    """)


def _feedback_datasources_and_source(connection: sqlite3.Connection) -> None:
    """Two nullable columns, so a row rated before them reads None."""
    present = {row[1] for row in connection.execute("PRAGMA table_info(feedback);")}
    for column in ("datasource_ids", "source"):
        if column not in present:
            connection.execute(f"ALTER TABLE feedback ADD COLUMN {column} TEXT;")


def _immutable_snapshots_and_locks(connection: sqlite3.Connection) -> None:
    _script(connection, """
        CREATE TRIGGER IF NOT EXISTS schema_snapshots_are_immutable
            BEFORE UPDATE ON schema_snapshots
            BEGIN SELECT RAISE(ABORT, 'schema snapshots are immutable'); END;
        CREATE TABLE IF NOT EXISTS locks (
            name TEXT PRIMARY KEY,
            holder TEXT NOT NULL,
            expires_at REAL NOT NULL
        );
    """)


def _schema_diffs(connection: sqlite3.Connection) -> None:
    """The structural diff that introduced each version, beside its snapshot (D31)."""
    _script(connection, """
        CREATE TABLE IF NOT EXISTS schema_diffs (
            datasource_id TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            diff_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (datasource_id, schema_version)
        );
    """)


MIGRATIONS: List[Tuple[str, Callable[[sqlite3.Connection], None]]] = [
    ("0001_adopt_0_5_tables", _adopt_0_5),
    ("0002_feedback_datasources_and_source", _feedback_datasources_and_source),
    ("0003_immutable_snapshots_and_locks", _immutable_snapshots_and_locks),
    ("0004_schema_diffs", _schema_diffs),
]

# -- feedback columns --------------------------------------------------------------

_FEEDBACK_COLUMNS = ("trace_id", "created_at", "rating", "note", "question", "role", "status", "sql",
                     "error_codes", "retries", "validator_failures", "plan_cache_hits", "sub_queries",
                     "models", "engine_version", "datasource_ids", "source")
_JSON_COLUMNS = ("sql", "error_codes", "models", "datasource_ids")


def _write_ahead_log(connection: sqlite3.Connection, wait_s: float = 5.0) -> None:
    """Switches the file to WAL, waiting out another connection switching it too.

    Changing the journal mode answers "database is locked" at once rather than
    through the busy timeout, so stores opening a new file together retry here.
    """
    deadline = time.monotonic() + wait_s
    while True:
        try:
            connection.execute("PRAGMA journal_mode=WAL;")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def _locked(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class SqliteState:
    """The built-in ``StateStore``, on one SQLite file (or ``:memory:``, for tests)."""

    interface_version: ClassVar[int] = 2
    slot: ClassVar[str] = "state"
    config_model: ClassVar[type] = NoKeys

    def __init__(self, path: Union[Path, str]) -> None:
        self.path = path if str(path) == ":memory:" else Path(path)
        self.clock: Callable[[], float] = time.time
        self._lock = threading.RLock()
        self._connection: Optional[sqlite3.Connection] = None

    @classmethod
    def from_config(cls, cfg: NoKeys, *, folder: Path) -> "SqliteState":
        """The store on ``<folder>/state.db``. Opens nothing until first used."""
        return cls(Path(folder) / "state.db")

    @_locked
    def _conn(self) -> sqlite3.Connection:
        if self._connection is None:
            if isinstance(self.path, Path):
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(str(self.path), check_same_thread=False)
            _write_ahead_log(self._connection)
        return self._connection

    @_locked
    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @_locked
    def migrate(self) -> List[str]:
        """Applies the migrations not yet recorded in ``nl2sql_meta``, in order; returns their names.

        All in one ``BEGIN IMMEDIATE`` transaction, which takes the write lock
        before ``nl2sql_meta`` is read: another store migrating the same file
        waits for it, then finds every migration applied.
        """
        connection = self._conn()
        connection.execute("BEGIN IMMEDIATE;")
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS nl2sql_meta ("
                               "migration TEXT PRIMARY KEY, applied_at INTEGER NOT NULL);")
            done = {row[0] for row in connection.execute("SELECT migration FROM nl2sql_meta;")}
            applied = []
            for name, migration in MIGRATIONS:
                if name in done:
                    continue
                migration(connection)
                connection.execute("INSERT INTO nl2sql_meta (migration, applied_at) VALUES (?, ?);",
                                   (name, int(self.clock())))
                applied.append(name)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        for name in applied:
            logger.info(f"Applied state migration {name}")
        return applied

    # -- snapshots ---------------------------------------------------------------

    @_locked
    def put_snapshot(self, snapshot: SchemaSnapshot) -> str:
        """Stores ``snapshot`` as a new immutable row; content already held returns its version."""
        datasource_id = snapshot.contract.datasource_id
        contract_json = json.dumps(snapshot.contract.model_dump(mode="json"), sort_keys=True)
        metadata_json = json.dumps(snapshot.metadata.model_dump(mode="json"), sort_keys=True)
        connection = self._conn()
        row = connection.execute(
            "SELECT schema_version FROM schema_snapshots "
            "WHERE datasource_id = ? AND contract_json = ? AND metadata_json = ? LIMIT 1;",
            (datasource_id, contract_json, metadata_json)).fetchone()
        if row:
            return row[0]
        now = self.clock()
        stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%d%H%M%S")
        digest = hashlib.sha256(f"{contract_json}\n{metadata_json}".encode()).hexdigest()
        version = f"{stamp}_{digest[:8]}"
        with connection:
            connection.execute(
                "INSERT INTO schema_snapshots (datasource_id, schema_version, fingerprint, contract_json, "
                "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?);",
                (datasource_id, version, generate_schema_fingerprint(snapshot.contract), contract_json,
                 metadata_json, int(now)))
        return version

    @_locked
    def get_snapshot(self, datasource_id: str, schema_version: str) -> Optional[SchemaSnapshot]:
        row = self._conn().execute(
            "SELECT contract_json, metadata_json FROM schema_snapshots "
            "WHERE datasource_id = ? AND schema_version = ?;", (datasource_id, schema_version)).fetchone()
        if not row:
            return None
        return SchemaSnapshot(contract=SchemaContract.model_validate_json(row[0]),
                              metadata=SchemaMetadata.model_validate_json(row[1]))

    @_locked
    def list_versions(self, datasource_id: str) -> List[str]:
        """The datasource's stored versions, newest first."""
        rows = self._conn().execute(
            "SELECT schema_version FROM schema_snapshots WHERE datasource_id = ? "
            "ORDER BY created_at DESC, rowid DESC;", (datasource_id,)).fetchall()
        return [row[0] for row in rows]

    @_locked
    def keep_snapshots(self, datasource_id: str, keep: Set[str]) -> int:
        """Deletes the datasource's other snapshots, and the cached plans and diffs of them; returns how many."""
        gone = [v for v in self.list_versions(datasource_id) if v not in keep]
        if not gone:
            return 0
        pairs = [(datasource_id, version) for version in gone]
        connection = self._conn()
        with connection:
            connection.executemany(
                "DELETE FROM schema_snapshots WHERE datasource_id = ? AND schema_version = ?;", pairs)
            connection.executemany(
                "DELETE FROM plan_cache WHERE datasource_id = ? AND schema_version = ?;", pairs)
            connection.executemany(
                "DELETE FROM schema_diffs WHERE datasource_id = ? AND schema_version = ?;", pairs)
        logger.info(f"Deleted {len(gone)} old schema snapshots of {datasource_id}: {', '.join(gone)}")
        return len(gone)

    # -- schema diffs ------------------------------------------------------------

    @_locked
    def put_schema_diff(self, diff: SchemaDiff) -> None:
        """Stores the diff that introduced ``diff.to_version``, replacing one stored for it before."""
        if diff.to_version is None:
            raise ValueError("A stored schema diff needs a to_version: the version it introduced.")
        connection = self._conn()
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO schema_diffs (datasource_id, schema_version, diff_json, created_at) "
                "VALUES (?, ?, ?, ?);",
                (diff.datasource_id, diff.to_version, diff.model_dump_json(), int(self.clock())))

    @_locked
    def get_schema_diff(self, datasource_id: str, schema_version: str) -> Optional[SchemaDiff]:
        row = self._conn().execute(
            "SELECT diff_json FROM schema_diffs WHERE datasource_id = ? AND schema_version = ?;",
            (datasource_id, schema_version)).fetchone()
        return SchemaDiff.model_validate_json(row[0]) if row else None

    # -- the plan cache ----------------------------------------------------------

    @_locked
    def get_plan(self, key: str, datasource_id: str, schema_version: str) -> Optional[str]:
        row = self._conn().execute(
            "SELECT plan_json FROM plan_cache WHERE question_key = ? AND datasource_id = ? "
            "AND schema_version = ?;", (key, datasource_id, schema_version)).fetchone()
        return row[0] if row else None

    @_locked
    def put_plan(self, key: str, datasource_id: str, schema_version: str, plan_json: str) -> None:
        connection = self._conn()
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO plan_cache (question_key, datasource_id, schema_version, plan_json, "
                "created_at) VALUES (?, ?, ?, ?, ?);",
                (key, datasource_id, schema_version, plan_json, int(self.clock())))

    @_locked
    def clear_plans(self) -> int:
        connection = self._conn()
        with connection:
            return connection.execute("DELETE FROM plan_cache;").rowcount

    # -- feedback ----------------------------------------------------------------

    @_locked
    def add_feedback(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Stores one rating, replacing an earlier one of the same run (``trace_id``)."""
        values = [json.dumps(row.get(c)) if c in _JSON_COLUMNS and row.get(c) is not None else row.get(c)
                  for c in _FEEDBACK_COLUMNS]
        connection = self._conn()
        with connection:
            connection.execute(
                f"INSERT OR REPLACE INTO feedback ({', '.join(_FEEDBACK_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _FEEDBACK_COLUMNS)});", values)
        return {c: row.get(c) for c in _FEEDBACK_COLUMNS}

    @_locked
    def list_feedback(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Every rating, newest first."""
        query = f"SELECT {', '.join(_FEEDBACK_COLUMNS)} FROM feedback ORDER BY created_at DESC, rowid DESC"
        params: tuple = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (int(limit),)
        rows = self._conn().execute(query, params).fetchall()
        out = []
        for values in rows:
            row = dict(zip(_FEEDBACK_COLUMNS, values))
            for column in _JSON_COLUMNS:
                if isinstance(row[column], str):
                    row[column] = json.loads(row[column])
            out.append(row)
        return out

    @_locked
    def clear_feedback(self) -> int:
        connection = self._conn()
        with connection:
            return connection.execute("DELETE FROM feedback;").rowcount

    # -- the lock ----------------------------------------------------------------

    @contextlib.contextmanager
    def lock(self, name: str, ttl_s: int) -> Iterator[None]:
        """A lease on ``name`` for the ``with`` block; it expires ``ttl_s`` seconds after it is taken.

        Raises:
            LockHeld: another holder has it and its lease has not expired.
        """
        holder = uuid4().hex
        with self._lock:
            connection = self._conn()
            connection.execute("BEGIN IMMEDIATE;")
            try:
                now = self.clock()
                row = connection.execute("SELECT expires_at FROM locks WHERE name = ?;", (name,)).fetchone()
                if row and row[0] > now:
                    until = datetime.fromtimestamp(row[0], tz=timezone.utc).isoformat(timespec="seconds")
                    raise LockHeld(f"'{name}' is held by another process until {until} (UTC). Wait for it "
                                   "to finish; a holder that stopped without releasing it lets go then.")
                connection.execute("INSERT OR REPLACE INTO locks (name, holder, expires_at) VALUES (?, ?, ?);",
                                   (name, holder, now + ttl_s))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        try:
            yield
        finally:
            with self._lock:
                connection = self._conn()
                with connection:
                    connection.execute("DELETE FROM locks WHERE name = ? AND holder = ?;", (name, holder))
