"""What the index holds: the datasource entry, example questions, and a snapshot
that matches the chunks built from it.

Found by reading the live Chinook index (tracker row 51.065): the datasource
entry was literally "Datasource: chinook / Domains: N/A / Examples: N/A", so
every question scored about the same against it, the weather included.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.schema import InMemorySchemaStore
from nl2sql.schema.sqlite_store import SqliteSchemaStore
from nl2sql_adapter_sdk.schema import (
    ColumnContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)

DESCRIPTION = "Chinook digital music store: artists, albums, tracks, genres"
QUESTIONS = ["Which artist has the most albums?", "Which genre sells the most tracks?"]


def _snapshot(description: str = "", row_count: int = 10) -> SchemaSnapshot:
    ref = TableRef(schema_name="main", table_name="Artist")
    contract = SchemaContract(
        datasource_id="chinook",
        engine_type="sqlite",
        tables={ref.full_name: TableContract(table=ref, columns={
            "ArtistId": ColumnContract(name="ArtistId", data_type="INTEGER"),
            "Name": ColumnContract(name="Name", data_type="NVARCHAR(120)"),
        })},
    )
    metadata = SchemaMetadata(
        datasource_id="chinook",
        engine_type="sqlite",
        description=description,
        tables={ref.full_name: TableMetadata(table=ref, columns={}, row_count=row_count)},
    )
    return SchemaSnapshot(contract=contract, metadata=metadata)


class _Adapter:
    datasource_id = "chinook"

    def __init__(self, snapshot=None):
        self.snapshot = snapshot or _snapshot()

    def fetch_schema_snapshot(self):
        return self.snapshot


class _RecordingStore:
    def __init__(self):
        self.chunks = []

    def refresh_schema_chunks(self, datasource_id, schema_version, chunks, evicted_versions, **_kw):
        self.chunks = list(chunks)
        return {"datasource_id": datasource_id, "schema_version": schema_version}


def _ctx(description=DESCRIPTION, questions=QUESTIONS, schema_store=None):
    return SimpleNamespace(
        vector_store=_RecordingStore(),
        schema_store=schema_store or InMemorySchemaStore(),
        config_manager=SimpleNamespace(
            get_example_questions=lambda _ds: list(questions),
            get_datasource_description=lambda _ds: description,
        ),
        llm_registry=SimpleNamespace(get_llm=lambda _name: (_ for _ in ()).throw(ValueError("no key"))),
    )


def _datasource_text(ctx) -> str:
    [chunk] = [c for c in ctx.vector_store.chunks if c.type == "schema.datasource"]
    return chunk.get_page_content()


# --- 1. the datasource entry gets its description -------------------------------


def test_the_datasource_chunk_contains_the_configured_description():
    ctx = _ctx()

    IndexingOrchestrator(ctx).index_datasource(_Adapter())

    assert DESCRIPTION in _datasource_text(ctx)


def test_the_datasource_chunk_contains_the_example_questions():
    ctx = _ctx()

    IndexingOrchestrator(ctx).index_datasource(_Adapter())

    text = _datasource_text(ctx)
    assert all(q in text for q in QUESTIONS)


def test_the_snapshot_stores_the_configured_description_too():
    """The panel and the planner read the snapshot; it should say the same."""
    ctx = _ctx()

    IndexingOrchestrator(ctx).index_datasource(_Adapter())

    assert ctx.schema_store.get_latest_snapshot("chinook").metadata.description == DESCRIPTION


def test_a_description_the_database_reports_is_kept_when_none_is_configured():
    ctx = _ctx(description=None)

    IndexingOrchestrator(ctx).index_datasource(_Adapter(_snapshot(description="from the catalog")))

    assert "from the catalog" in _datasource_text(ctx)


# --- 4. re-indexing an unchanged schema refreshes the stored metadata ----------


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path):
    if request.param == "sqlite":
        s = SqliteSchemaStore(tmp_path / "schema.db")
        yield s
        s._connection.close()
    else:
        yield InMemorySchemaStore()


def test_reregistering_an_unchanged_structure_keeps_the_version_and_updates_metadata(store):
    first, _ = store.register_snapshot(_snapshot(description="old", row_count=10))

    second, evicted = store.register_snapshot(_snapshot(description="new", row_count=11))

    assert second == first
    assert evicted == []
    stored = store.get_snapshot("chinook", first)
    assert stored.metadata.description == "new"
    assert next(iter(stored.metadata.tables.values())).row_count == 11
    assert store.get_latest_version("chinook") == first


def test_an_unchanged_structure_that_returns_after_a_change_becomes_latest_again(tmp_path):
    """Structure A, then B, then A again: A's version is reused, and it must be
    the latest, or the resolver compares chunks against B."""
    store = SqliteSchemaStore(tmp_path / "schema.db")
    a1, _ = store.register_snapshot(_snapshot())
    other = _snapshot()
    next(iter(other.contract.tables.values())).columns["Extra"] = ColumnContract(name="Extra", data_type="TEXT")
    store.register_snapshot(other)

    import time
    time.sleep(1.1)  # created_at has one-second resolution
    a2, _ = store.register_snapshot(_snapshot(description="back"))

    assert a2 == a1
    assert store.get_latest_version("chinook") == a1
    store._connection.close()


def test_health_sees_an_index_rebuilt_on_a_returning_structure_as_current(tmp_path):
    """#110's startup repair compares the index's schema version with the latest
    snapshot. After A, B, then A again, the index is built from A; A must be the
    latest, or every start would call the index stale and rebuild it."""
    import time

    from nl2sql.indexing.health import summarize

    store = SqliteSchemaStore(tmp_path / "schema.db")
    a, _ = store.register_snapshot(_snapshot())
    other = _snapshot()
    next(iter(other.contract.tables.values())).columns["Extra"] = ColumnContract(name="Extra", data_type="TEXT")
    store.register_snapshot(other)
    time.sleep(1.1)
    store.register_snapshot(_snapshot())

    entries = [{"type": "schema.datasource", "datasource_id": "chinook", "schema_version": a}]
    health = summarize(entries, store.get_latest_version, ["chinook"])

    assert health.status == "ok", health.problems
    store.close()
