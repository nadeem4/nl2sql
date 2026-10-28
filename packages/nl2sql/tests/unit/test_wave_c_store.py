"""The state store at interface version 4: conversations, workspaces, dashboards, stored chart results and API
keys (docs/architecture/wave_c.md, section 10), and migration 6."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from nl2sql import Registry
from nl2sql.registry import INTERFACE_VERSIONS, RegistryError
from nl2sql.storage.sqlite import MIGRATIONS, SqliteState

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store():
    state = SqliteState(":memory:")
    state.migrate()
    yield state
    state.close()


def _stamp(days_ago: float = 0) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _conversation(cid, *, user="ada", tenant="acme", days_ago=0):
    return {"id": cid, "tenant_id": tenant, "user_id": user, "title": cid, "updated_at": _stamp(days_ago),
            "turns": []}


def _dashboard(did, *, status="saved", user="ada", tenant="acme", days_ago=0, schedule=None):
    return {"id": did, "title": did, "status": status, "tenant_id": tenant, "user_id": user,
            "updated_at": _stamp(days_ago), "schedule": schedule, "tiles": []}


def test_state_store_round_trips_conversations_dashboards_and_keys(store):
    # conversations: newest first, per owner, None is an owner of its own
    for doc in (_conversation("c1", days_ago=2), _conversation("c2", days_ago=1), _conversation("c3"),
                _conversation("other", user="bob"), _conversation("anon", user=None),
                _conversation("elsewhere", tenant="globex")):
        store.put_conversation(doc)
    assert store.get_conversation("c1") == _conversation("c1", days_ago=2)
    assert store.get_conversation("nope") is None
    assert [d["id"] for d in store.list_conversations("acme", "ada", 10)] == ["c3", "c2", "c1"]
    assert [d["id"] for d in store.list_conversations("acme", "ada", 2)] == ["c3", "c2"]
    assert [d["id"] for d in store.list_conversations("acme", None, 10)] == ["anon"], "no user is an owner"
    store.put_conversation({**_conversation("c1", days_ago=2), "title": "renamed"})
    assert store.get_conversation("c1")["title"] == "renamed", "putting an id again replaces it"
    assert store.delete_conversation("c1") is True and store.delete_conversation("c1") is False
    assert store.get_conversation("c1") is None

    # workspaces
    store.put_workspace({"id": "w1", "name": "General", "tenant_id": "acme", "user_id": "ada"})
    store.put_workspace({"id": "w2", "name": "Ops", "tenant_id": "acme", "user_id": "bob"})
    assert store.get_workspace("w1")["name"] == "General" and store.get_workspace("zz") is None
    assert [w["id"] for w in store.list_workspaces("acme", "ada")] == ["w1"]
    assert store.delete_workspace("w1") is True and store.delete_workspace("w1") is False

    # dashboards, with their stored chart results
    scheduled = {"every_minutes": 60, "state": "active"}
    store.put_dashboard(_dashboard("d1", schedule=scheduled))
    store.put_dashboard(_dashboard("d2", status="draft"))
    store.put_dashboard(_dashboard("d3", user="bob"))
    assert store.get_dashboard("d1")["schedule"] == scheduled and store.get_dashboard("zz") is None
    assert sorted(d["id"] for d in store.list_dashboards("acme", "ada")) == ["d1", "d2"], "every status"
    assert [d["id"] for d in store.list_scheduled_dashboards()] == ["d1"]
    store.put_dashboard(_dashboard("d1", schedule=None))
    assert store.list_scheduled_dashboards() == [], "dropping the schedule drops it from the due list"

    store.put_tile_runs("d1", [{"tile_id": "t1", "at": _stamp(), "status": "ok"},
                               {"tile_id": "t2", "at": _stamp(), "status": "ok"}])
    store.put_tile_runs("d1", [{"tile_id": "t2", "at": _stamp(), "status": "error"}])
    runs = {r["tile_id"]: r["status"] for r in store.get_tile_runs("d1")}
    assert runs == {"t1": "ok", "t2": "error"}, "only the listed charts' results are replaced"
    assert store.get_tile_runs("d2") == []
    assert store.delete_dashboard("d1") is True and store.delete_dashboard("d1") is False
    assert store.get_tile_runs("d1") == [], "results go with the dashboard"

    # API keys: a row with a hash, never a secret; revoking is a put
    store.put_api_key({"id": "ab12cd34", "hash": "x" * 64, "user_id": "ada", "revoked_at": None})
    store.put_api_key({"id": "ef56gh78", "hash": "y" * 64, "user_id": "bob", "revoked_at": None})
    assert store.get_api_key("ab12cd34")["hash"] == "x" * 64 and store.get_api_key("zz") is None
    store.put_api_key({"id": "ab12cd34", "hash": "x" * 64, "user_id": "ada", "revoked_at": _stamp()})
    assert store.get_api_key("ab12cd34")["revoked_at"] == _stamp()
    assert sorted(k["id"] for k in store.list_api_keys()) == ["ab12cd34", "ef56gh78"]


def test_purges_delete_old_conversations_and_old_drafts_only(store):
    for doc in (_conversation("old", days_ago=40), _conversation("new", days_ago=1)):
        store.put_conversation(doc)
    assert store.purge_conversations(NOW - timedelta(days=30)) == 1
    assert store.get_conversation("old") is None and store.get_conversation("new") is not None

    store.put_dashboard(_dashboard("old_draft", status="draft", days_ago=40))
    store.put_dashboard(_dashboard("new_draft", status="draft", days_ago=1))
    store.put_dashboard(_dashboard("old_saved", status="saved", days_ago=400))
    store.put_tile_runs("old_draft", [{"tile_id": "t", "at": _stamp(40)}])
    assert store.purge_dashboards(NOW - timedelta(days=30)) == 1
    assert store.get_dashboard("old_draft") is None and store.get_tile_runs("old_draft") == []
    assert store.get_dashboard("new_draft") and store.get_dashboard("old_saved"), "saved is never purged"


def test_a_naive_or_z_stamp_compares_as_utc(store):
    store.put_conversation({**_conversation("z"), "updated_at": "2026-09-01T00:00:00Z"})
    store.put_conversation({**_conversation("naive"), "updated_at": "2026-09-01T00:00:00"})
    assert store.purge_conversations(datetime(2026, 10, 1, tzinfo=timezone.utc)) == 2


def test_migration_6_adopts_a_0_8_state_file(tmp_path):
    """A state.db that ran migrations 1 to 5 (release 0.8) gains only Wave C's tables, keeping its rows."""
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE nl2sql_meta (migration TEXT PRIMARY KEY, applied_at INTEGER NOT NULL);")
        for name, migration in MIGRATIONS[:5]:
            migration(connection)
            connection.execute("INSERT INTO nl2sql_meta VALUES (?, 0);", (name,))
        assert [name for name, _ in MIGRATIONS[:5]][-1] == "0005_proposals"
        connection.execute("INSERT INTO plan_cache VALUES ('q', 'ds', 'v1', '{}', 0);")

    state = SqliteState(path)
    assert state.migrate() == ["0006_conversations_dashboards_and_keys"]
    assert state.migrate() == []
    assert state.get_plan("q", "ds", "v1") == "{}", "0.8's rows are kept"
    state.put_conversation(_conversation("c1"))
    assert state.get_conversation("c1") is not None
    state.close()
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"conversations", "workspaces", "dashboards", "tile_runs", "api_keys"} <= tables


def test_a_fresh_file_applies_all_six_migrations(tmp_path):
    state = SqliteState(tmp_path / "state.db")
    assert len(state.migrate()) == 6 == len(MIGRATIONS)
    state.close()


def test_store_version_3_is_rejected():
    assert INTERFACE_VERSIONS["store"] == 4
    registry = Registry.empty()
    registry.add_store("old", type("Store", (), {"interface_version": 3}))
    with pytest.raises(RegistryError, match="store interface 3; this engine needs 4"):
        registry.load("store", "old")
    for name in ("chroma", "sqlite", "files", "local"):
        assert Registry.default().load("store", name).interface_version == 4, name
