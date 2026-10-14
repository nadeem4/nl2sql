"""What the index holds: the datasource entry, example questions, and a snapshot
that matches the chunks built from it.

Found by reading the live Chinook index (tracker row 51.065): the datasource
entry was literally "Datasource: chinook / Domains: N/A / Examples: N/A", so
every question scored about the same against it, the weather included.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from index_helpers import TestIndex  # same directory
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.schema import InMemorySchemaStore
from nl2sql.schema.sqlite_store import SqliteSchemaStore
from nl2sql_sdk.stores import Filter
from nl2sql_sdk import IndexScope
from nl2sql_sdk.schema import (
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

    def fetch_schema_snapshot(self, scope, on_table=None):
        return self.snapshot


def _ctx(tmp_path, description=DESCRIPTION, questions=QUESTIONS, schema_store=None):
    ctx = TestIndex(tmp_path, FakeEmbeddings(size=8), schema_store=schema_store or InMemorySchemaStore()).ctx
    ctx.ds_registry = SimpleNamespace(
        get_index_scope=lambda _ds: IndexScope(), get_sample_questions=lambda _ds: list(questions),
        get_description=lambda _ds: description,
    )
    ctx.llm_registry = SimpleNamespace(get_llm=lambda _name: (_ for _ in ()).throw(ValueError("no key")))
    ctx.curated = None
    return ctx


def _live_version(ctx) -> str:
    return ctx.index.pointers(ctx.namespace)["chinook"].schema_version


def _datasource_text(ctx) -> str:
    entries = ctx.index.get(ctx.namespace, Filter(eq={"type": "schema.datasource"}))
    [entry] = [e for e in entries if e.metadata["build_id"] == ctx.index.pointers(ctx.namespace)["chinook"].build_id]
    return entry.text


# --- 1. the datasource entry gets its description -------------------------------


def test_the_datasource_chunk_contains_the_configured_description(tmp_path):
    ctx = _ctx(tmp_path)

    IndexingOrchestrator(ctx).index_datasource(_Adapter())

    assert DESCRIPTION in _datasource_text(ctx)


def test_the_datasource_chunk_contains_the_example_questions(tmp_path):
    ctx = _ctx(tmp_path)

    IndexingOrchestrator(ctx).index_datasource(_Adapter())

    text = _datasource_text(ctx)
    assert all(q in text for q in QUESTIONS)


def test_the_snapshot_stores_the_configured_description_too(tmp_path):
    """The panel and the planner read the live build's snapshot; it should say the same."""
    ctx = _ctx(tmp_path)

    IndexingOrchestrator(ctx).index_datasource(_Adapter())

    assert ctx.schema_store.get_snapshot("chinook", _live_version(ctx)).metadata.description == DESCRIPTION


def test_a_description_the_database_reports_is_kept_when_none_is_configured(tmp_path):
    ctx = _ctx(tmp_path, description=None)

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
    assert store.list_versions("chinook") == [first]


def _changed() -> SchemaSnapshot:
    other = _snapshot()
    next(iter(other.contract.tables.values())).columns["Extra"] = ColumnContract(name="Extra", data_type="TEXT")
    return other


def test_a_structure_that_returns_after_a_change_reuses_its_version_and_the_pointer_names_it(tmp_path):
    """Structure A, then B, then A again: A's version is reused, and the build
    pointer names it, so readers use A's snapshot and not B's."""
    store = SqliteSchemaStore(tmp_path / "schema.db")
    ctx = _ctx(tmp_path, schema_store=store)
    orchestrator = IndexingOrchestrator(ctx)
    orchestrator.index_datasource(_Adapter())
    a1 = _live_version(ctx)
    orchestrator.index_datasource(_Adapter(_changed()))
    b = _live_version(ctx)

    orchestrator.index_datasource(_Adapter(_snapshot(description="back")))

    assert b != a1
    assert _live_version(ctx) == a1
    assert "Extra" not in next(iter(store.get_snapshot("chinook", a1).contract.tables.values())).columns
    store.close()


def test_health_sees_an_index_rebuilt_on_a_returning_structure_as_current(tmp_path):
    """After A, B, then A again, the index is built from A; health reads the
    pointer, so the index is current and no start would rebuild it."""
    from nl2sql.indexing.health import index_health

    store = SqliteSchemaStore(tmp_path / "schema.db")
    ctx = _ctx(tmp_path, schema_store=store)
    orchestrator = IndexingOrchestrator(ctx)
    for snapshot in (_snapshot(), _changed(), _snapshot()):
        orchestrator.index_datasource(_Adapter(snapshot))

    health = index_health(ctx.index, ctx.namespace, store, ["chinook"])

    assert health.status == "ok", health.problems
    store.close()
