"""``nl2sql index`` writes curated entries with the schema, and
``nl2sql index --curated-only`` refreshes them without the schema.

A full index of a datasource reads its schema and, with enrichment on, calls
the enrichment model. Editing ``curated.yaml`` needs neither: the curated-only
refresh copies the active build's schema entries as they are and embeds only
the verified queries.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.embeddings import FakeEmbeddings

from nl2sql.config import Curated
from nl2sql.indexing import enrichment_service, orchestrator as orchestrator_module
from nl2sql.indexing.curated import curated_hash
from nl2sql.indexing.rebuild import rebuild_index
from nl2sql.indexing.vector_store import VectorStore
from nl2sql.schema import InMemorySchemaStore
from nl2sql_sdk.schema import (
    ColumnContract,
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
)


def _snapshot() -> SchemaSnapshot:
    ref = TableRef(schema_name="main", table_name="Customer")
    contract = SchemaContract(
        datasource_id="chinook", engine_type="sqlite",
        tables={ref.full_name: TableContract(table=ref, columns={
            "CustomerId": ColumnContract(name="CustomerId", data_type="int")})},
    )
    metadata = SchemaMetadata(datasource_id="chinook", engine_type="sqlite",
                              tables={ref.full_name: TableMetadata(table=ref, columns={})})
    return SchemaSnapshot(contract=contract, metadata=metadata)


def _curated(*questions) -> Curated:
    return Curated.model_validate({"datasources": {"chinook": {"verified_queries": [{
        "id": "top_customers", "description": "Customers by revenue",
        "questions": list(questions), "sql": "SELECT 1",
    }]}}})


class _Adapter:
    datasource_id = "chinook"

    def __init__(self):
        self.reads = 0

    def fetch_schema_snapshot(self):
        self.reads += 1
        return _snapshot()


class _NoLLM:
    def __getattr__(self, name):
        raise AssertionError(f"the LLM registry was used ({name})")


def _ctx(tmp_path, curated):
    adapter = _Adapter()
    store = VectorStore("nl2sql_store", str(tmp_path / "vs"), embeddings=FakeEmbeddings(size=8))
    return SimpleNamespace(
        vector_store=store,
        schema_store=InMemorySchemaStore(),
        llm_registry=_NoLLM(),
        ds_registry=SimpleNamespace(list_adapters=lambda: [adapter], get_sample_questions=lambda _ds: [],
                                    get_description=lambda _ds: "Music store"),
        curated=curated,
    ), adapter


def _active(store, kind=None):
    clauses = [{"datasource_id": "chinook"}, {"build_id": store.active_builds()["chinook"]}]
    got = store.vectorstore._collection.get(where={"$and": clauses}, include=["documents", "metadatas", "embeddings"])
    rows = [(d, m, tuple(e)) for d, m, e in zip(got["documents"], got["metadatas"], got["embeddings"])]
    if kind == "curated":
        return sorted(d for d, m, _ in rows if m["type"] == "curated.verified_query")
    return sorted((d, v) for d, m, v in rows if m["type"] != "curated.verified_query")


def test_a_full_index_writes_the_curated_entries_and_their_hash(tmp_path):
    ctx, _ = _ctx(tmp_path, _curated("Top customers"))

    result = rebuild_index(ctx, enrich=False)

    assert result.ok
    assert _active(ctx.vector_store, "curated") == ["Customers by revenue", "Top customers"]
    assert ctx.vector_store._live_metadata()["curated_hash:chinook"] == curated_hash(
        ctx.curated.datasources["chinook"])


def test_a_curated_only_refresh_makes_no_model_call_and_keeps_the_schema(tmp_path, monkeypatch):
    ctx, adapter = _ctx(tmp_path, _curated("Top customers"))
    rebuild_index(ctx, enrich=False)
    schema_before = _active(ctx.vector_store)
    reads_before = adapter.reads

    def _no_enrichment(*_a, **_k):
        raise AssertionError("enrichment ran")

    monkeypatch.setattr(enrichment_service, "enrich_schema_snapshot", _no_enrichment)
    monkeypatch.setattr(orchestrator_module, "enrich_schema_snapshot", _no_enrichment)
    ctx.curated = _curated("Top customers", "Best customers")

    result = rebuild_index(ctx, enrich=True, curated_only=True)

    assert result.ok, result.errors
    assert adapter.reads == reads_before
    assert _active(ctx.vector_store) == schema_before
    assert _active(ctx.vector_store, "curated") == ["Best customers", "Customers by revenue", "Top customers"]
    assert ctx.vector_store._live_metadata()["curated_hash:chinook"] == curated_hash(
        ctx.curated.datasources["chinook"])
    assert result.stats[0]["curated.verified_query"] == 3


def test_removing_the_curated_file_and_refreshing_drops_the_entries(tmp_path):
    ctx, _ = _ctx(tmp_path, _curated("Top customers"))
    rebuild_index(ctx, enrich=False)
    ctx.curated = None

    assert rebuild_index(ctx, curated_only=True).ok
    assert _active(ctx.vector_store, "curated") == []


def test_a_curated_only_refresh_of_an_unindexed_datasource_fails_for_that_datasource(tmp_path):
    ctx, _ = _ctx(tmp_path, _curated("Top customers"))

    result = rebuild_index(ctx, curated_only=True)

    assert not result.ok
    assert "nl2sql index" in result.errors[0]["error"]


def test_curated_only_and_full_do_not_combine(tmp_path):
    ctx, _ = _ctx(tmp_path, None)
    with pytest.raises(ValueError, match="curated-only"):
        rebuild_index(ctx, full=True, curated_only=True)
