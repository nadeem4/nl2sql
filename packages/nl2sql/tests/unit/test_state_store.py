"""The ``sqlite`` state store: immutable snapshots, the plan cache, feedback, migrations and the lock.

See docs/architecture/phase2.md, section 4. Every test uses a real store, on
a file in ``tmp_path`` or on ``:memory:``.
"""
from __future__ import annotations

import os
import sqlite3
import stat
import threading
import time

import pytest

from nl2sql.storage.sqlite import MIGRATIONS, LockHeld, SqliteState
from nl2sql_sdk.schema import (
    ColumnContract,
    SchemaContract,
    SchemaDiff,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)


def _snapshot(table_name: str = "users", rows: int = 10) -> SchemaSnapshot:
    ref = TableRef(schema_name="public", table_name=table_name)
    contract = SchemaContract(datasource_id="ds1", engine_type="postgres", tables={
        ref.full_name: TableContract(table=ref, foreign_keys=[], columns={
            "id": ColumnContract(name="id", data_type="int", is_nullable=False, is_primary_key=True)})})
    metadata = SchemaMetadata(datasource_id="ds1", engine_type="postgres", tables={
        ref.full_name: TableMetadata(table=ref, row_count=rows, columns={})})
    return SchemaSnapshot(contract=contract, metadata=metadata)


@pytest.fixture
def store():
    state = SqliteState(":memory:")
    state.migrate()
    yield state
    state.close()


# -- snapshots -------------------------------------------------------------------


def test_a_snapshot_round_trips(store):
    version = store.put_snapshot(_snapshot())

    fetched = store.get_snapshot("ds1", version)

    assert fetched == _snapshot()
    assert store.get_snapshot("ds1", "nope") is None


def test_snapshot_rows_are_never_updated(store):
    """A snapshot is an immutable row keyed by its version: new content is a new row.

    0.5 refreshed an unchanged structure's metadata in place, so a run pinned
    to that version read statistics its index was never built from.
    """
    first = store.put_snapshot(_snapshot(rows=10))

    second = store.put_snapshot(_snapshot(rows=99))   # same structure, new statistics
    again = store.put_snapshot(_snapshot(rows=10))    # the same content once more

    assert second != first
    assert again == first
    assert store.get_snapshot("ds1", first).metadata.tables["[public].[users]"].row_count == 10
    assert store.get_snapshot("ds1", second).metadata.tables["[public].[users]"].row_count == 99
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        store._conn().execute("UPDATE schema_snapshots SET metadata_json = '{}'")


def test_list_versions_is_newest_first(store):
    v1 = store.put_snapshot(_snapshot("users"))
    v2 = store.put_snapshot(_snapshot("orders"))
    v3 = store.put_snapshot(_snapshot("payments"))

    assert store.list_versions("ds1") == [v3, v2, v1]
    assert store.list_versions("other") == []


def test_keep_snapshots_deletes_the_rest_and_their_plans(store):
    v1 = store.put_snapshot(_snapshot("users"))
    v2 = store.put_snapshot(_snapshot("orders"))
    store.put_plan("q", "ds1", v1, "{}")
    store.put_plan("q", "ds1", v2, "{}")

    deleted = store.keep_snapshots("ds1", {v2})

    assert deleted == 1
    assert store.list_versions("ds1") == [v2]
    assert store.get_plan("q", "ds1", v1) is None
    assert store.get_plan("q", "ds1", v2) == "{}"


# -- schema diffs (store interface version 2, D31) ---------------------------------


def _diff(to_version: str, from_version: str = "v0") -> SchemaDiff:
    return SchemaDiff(datasource_id="ds1", from_version=from_version, to_version=to_version,
                      tables_added=[TableRef(schema_name="public", table_name="orders")],
                      engine_type=("sqlite", "postgres"))


def test_a_schema_diff_round_trips_keyed_by_datasource_and_version(store):
    version = store.put_snapshot(_snapshot("orders"))

    store.put_schema_diff(_diff(version))

    assert store.get_schema_diff("ds1", version) == _diff(version)
    assert store.get_schema_diff("ds1", "nope") is None
    assert store.get_schema_diff("other", version) is None
    store.put_schema_diff(_diff(version, from_version="v9"))   # recomputed: replaces
    assert store.get_schema_diff("ds1", version).from_version == "v9"


def test_a_diff_without_a_to_version_is_refused(store):
    with pytest.raises(ValueError, match="to_version"):
        store.put_schema_diff(_diff(None))


def test_diff_is_pruned_with_its_snapshot(store):
    v1 = store.put_snapshot(_snapshot("users"))
    v2 = store.put_snapshot(_snapshot("orders"))
    store.put_schema_diff(_diff(v1))
    store.put_schema_diff(_diff(v2))

    store.keep_snapshots("ds1", {v2})

    assert store.get_schema_diff("ds1", v1) is None
    assert store.get_schema_diff("ds1", v2) is not None


def test_the_state_store_implements_store_interface_version_2():
    assert SqliteState.interface_version == 2


# -- the plan cache --------------------------------------------------------------


def test_plans_are_kept_by_key_datasource_and_version_and_cleared_alone(store):
    version = store.put_snapshot(_snapshot())
    store.put_plan("q", "ds1", version, '{"a": 1}')
    store.put_plan("q", "ds1", version, '{"a": 2}')

    assert store.get_plan("q", "ds1", version) == '{"a": 2}'
    assert store.get_plan("q", "ds1", "other") is None
    assert store.clear_plans() == 1
    assert store.get_plan("q", "ds1", version) is None
    assert store.get_snapshot("ds1", version) is not None


# -- feedback --------------------------------------------------------------------


def _row(trace_id: str = "t1", rating: str = "up", created_at: int = 1, **extra) -> dict:
    return {"trace_id": trace_id, "created_at": created_at, "rating": rating, "note": None,
            "question": "How many?", "role": "admin", "status": "success", "sql": ["SELECT 1"],
            "error_codes": [], "retries": 0, "validator_failures": 0, "plan_cache_hits": 0,
            "sub_queries": 1, "models": {"astplanner": {"model": "m"}}, "engine_version": "x",
            "datasource_ids": ["chinook"], "source": "generated", **extra}


def test_feedback_is_added_replaced_listed_and_cleared(store):
    store.add_feedback(_row("t1", "up", created_at=1))
    store.add_feedback(_row("t2", "up", created_at=2))
    stored = store.add_feedback(_row("t1", "down", created_at=3, note="wrong"))

    rows = store.list_feedback()

    assert stored == _row("t1", "down", created_at=3, note="wrong")
    assert [(r["trace_id"], r["rating"]) for r in rows] == [("t1", "down"), ("t2", "up")]
    assert rows[0]["sql"] == ["SELECT 1"] and rows[0]["models"] == {"astplanner": {"model": "m"}}
    assert len(store.list_feedback(limit=1)) == 1
    assert store.clear_feedback() == 2
    assert store.list_feedback() == []


# -- migrations ------------------------------------------------------------------


def test_migrate_applies_each_migration_once(tmp_path):
    state = SqliteState(tmp_path / "state.db")
    applied = state.migrate()
    again = state.migrate()
    state.close()

    assert applied and applied == sorted(applied)
    assert applied[-1] == "0004_schema_diffs"
    assert again == []


def test_building_the_store_writes_nothing(tmp_path):
    SqliteState.from_config(SqliteState.config_model(), folder=tmp_path / "data")

    assert not (tmp_path / "data").exists()


# 0.5's three tables, as 0.5 created them.
SCHEMA_0_5 = """
CREATE TABLE schema_snapshots (
    datasource_id TEXT NOT NULL, schema_version TEXT NOT NULL, fingerprint TEXT NOT NULL,
    contract_json TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY (datasource_id, schema_version));
CREATE INDEX idx_schema_snapshots_fingerprint ON schema_snapshots (datasource_id, fingerprint);
CREATE INDEX idx_schema_snapshots_created_at ON schema_snapshots (datasource_id, created_at);
CREATE TABLE plan_cache (
    question_key TEXT NOT NULL, datasource_id TEXT NOT NULL, schema_version TEXT NOT NULL,
    plan_json TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY (question_key, datasource_id, schema_version));
CREATE TABLE feedback (
    trace_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')), note TEXT, question TEXT NOT NULL,
    role TEXT NOT NULL, status TEXT NOT NULL, sql TEXT NOT NULL, error_codes TEXT NOT NULL,
    retries INTEGER NOT NULL DEFAULT 0, validator_failures INTEGER NOT NULL DEFAULT 0,
    plan_cache_hits INTEGER NOT NULL DEFAULT 0, sub_queries INTEGER NOT NULL DEFAULT 0,
    models TEXT NOT NULL, engine_version TEXT NOT NULL);
"""


def test_a_05_state_file_is_adopted(tmp_path):
    path = tmp_path / "state.db"
    snapshot = _snapshot()
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_0_5)
        connection.execute("INSERT INTO schema_snapshots VALUES ('ds1', 'v05', 'fp', ?, ?, 1)",
                           (snapshot.contract.model_dump_json(), snapshot.metadata.model_dump_json()))
        connection.execute("INSERT INTO plan_cache VALUES ('q', 'ds1', 'v05', '{}', 1)")
        connection.execute("INSERT INTO feedback VALUES ('old', 1, 'up', NULL, 'How many?', 'admin', "
                           "'success', '[\"SELECT 1\"]', '[]', 0, 0, 0, 1, '{}', 'x')")
    connection.close()

    state = SqliteState(path)
    applied = state.migrate()

    assert applied
    assert state.get_snapshot("ds1", "v05") == snapshot
    assert state.get_plan("q", "ds1", "v05") == "{}"
    old = state.list_feedback()[0]
    assert old["question"] == "How many?" and old["datasource_ids"] is None and old["source"] is None
    state.add_feedback(_row("new", created_at=2))
    assert len(state.list_feedback()) == 2
    with state.lock("index", ttl_s=60):
        pass
    state.close()


# -- the lock --------------------------------------------------------------------


def test_lock_blocks_a_second_holder_and_expires(tmp_path):
    path = tmp_path / "state.db"
    first, second = SqliteState(path), SqliteState(path)
    first.migrate()
    now = [1000.0]
    first.clock = second.clock = lambda: now[0]

    with first.lock("index", ttl_s=60):
        with pytest.raises(LockHeld, match="index"):
            with second.lock("index", ttl_s=60):
                pass
        with second.lock("other", ttl_s=60):   # another name is another lock
            pass
        now[0] += 61                            # the holder died: its lease runs out
        with second.lock("index", ttl_s=60):
            pass

    with second.lock("index", ttl_s=60):        # released on leaving the block
        pass
    first.close()
    second.close()


def test_a_lock_is_released_when_its_block_raises(store):
    with pytest.raises(RuntimeError):
        with store.lock("index", ttl_s=60):
            raise RuntimeError("the build failed")

    with store.lock("index", ttl_s=60):
        pass


# -- threads ---------------------------------------------------------------------


def test_one_store_serves_many_threads_at_once(tmp_path):
    """The playground reads the schema and the index health on two threadpool
    workers at once, through the one store: a shared sqlite3 connection used
    from two threads at the same moment raised ``InterfaceError``."""
    state = SqliteState(tmp_path / "state.db")
    state.migrate()
    version = state.put_snapshot(_snapshot())
    failures = []
    start = threading.Barrier(8)

    def reader():
        start.wait()
        try:
            for _ in range(200):
                assert state.list_versions("ds1") == [version]
                assert state.get_snapshot("ds1", version) is not None
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            failures.append(repr(exc))

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    state.close()

    assert failures == []


def test_stores_opening_one_fresh_file_at_once_migrate_it_exactly_once(tmp_path):
    """Two engines (or an engine and a CLI run) opening a new ``state.db`` at
    the same moment: each read "nothing applied" before the other wrote, so the
    second applied a migration again and failed on its ``nl2sql_meta`` row or
    on a column the first had just added."""
    for attempt in range(10):
        path = tmp_path / f"state-{attempt}.db"
        stores = [SqliteState(path) for _ in range(6)]
        failures = []
        start = threading.Barrier(len(stores))

        def open_and_migrate(store):
            start.wait()
            try:
                store.migrate()
            except Exception as exc:  # noqa: BLE001 - collected and asserted below
                failures.append(repr(exc))

        threads = [threading.Thread(target=open_and_migrate, args=(s,)) for s in stores]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for store in stores:
            store.close()

        assert failures == []
        with sqlite3.connect(path) as connection:
            rows = [r[0] for r in connection.execute("SELECT migration FROM nl2sql_meta ORDER BY migration")]
        connection.close()
        assert rows == [name for name, _ in MIGRATIONS]


def test_an_up_to_date_store_migrates_without_waiting_for_a_writer(tmp_path):
    """Every engine start after the first finds nothing pending: it must not
    queue behind another connection's write transaction to learn that."""
    path = tmp_path / "state.db"
    first = SqliteState(path)
    first.migrate()
    first.close()

    writer = sqlite3.connect(path, timeout=0)
    writer.execute("BEGIN IMMEDIATE;")
    try:
        store = SqliteState(path)
        started = time.monotonic()
        assert store.migrate() == []
        assert time.monotonic() - started < 1
        store.close()
    finally:
        writer.rollback()
        writer.close()


def test_an_up_to_date_read_only_store_opens(tmp_path):
    path = tmp_path / "state.db"
    first = SqliteState(path)
    first.migrate()
    version = first.put_snapshot(_snapshot())
    first.close()
    os.chmod(path, stat.S_IREAD)
    try:
        store = SqliteState(path)
        assert store.migrate() == []
        assert store.list_versions("ds1") == [version]
        store.close()
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_a_failed_switch_to_wal_leaves_no_connection_behind(tmp_path, monkeypatch):
    import nl2sql.storage.sqlite as module

    def refuse(connection, wait_s=5.0):
        raise sqlite3.OperationalError("database is locked")

    store = SqliteState(tmp_path / "state.db")
    monkeypatch.setattr(module, "_write_ahead_log", refuse)
    with pytest.raises(sqlite3.OperationalError):
        store.migrate()
    monkeypatch.undo()
    assert store._connection is None
    assert store.migrate()  # the next use opens afresh, in WAL
    assert store._conn().execute("PRAGMA journal_mode;").fetchone()[0] == "wal"
    store.close()


def test_a_migration_script_takes_one_statement_per_line():
    from nl2sql.storage.sqlite import _script

    connection = sqlite3.connect(":memory:")
    _script(connection, """
        CREATE TABLE a (x);
        CREATE TRIGGER t BEFORE UPDATE ON a
            BEGIN SELECT RAISE(ABORT, 'no; never'); END;
    """)
    with pytest.raises(sqlite3.ProgrammingError):
        _script(connection, "CREATE TABLE b (x); CREATE TABLE c (x);\n")
    connection.close()
