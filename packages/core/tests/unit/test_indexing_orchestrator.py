import pytest
from unittest.mock import MagicMock, patch

from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.schema import (
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
    ColumnContract,
)


def make_snapshot(
    datasource_id: str = "ds1",
    engine_type: str = "postgres",
    table_name: str = "users",
) -> SchemaSnapshot:
    table_ref = TableRef(schema_name="public", table_name=table_name)
    column = ColumnContract(name="id", data_type="int", is_nullable=False, is_primary_key=True)
    table_contract = TableContract(table=table_ref, columns={"id": column})
    table_metadata = TableMetadata(table=table_ref, row_count=10)

    contract = SchemaContract(
        datasource_id=datasource_id,
        engine_type=engine_type,
        tables={table_ref.full_name: table_contract},
    )
    metadata = SchemaMetadata(
        datasource_id=datasource_id,
        engine_type=engine_type,
        tables={table_ref.full_name: table_metadata},
    )
    return SchemaSnapshot(contract=contract, metadata=metadata)


def test_index_datasource_registers_and_refreshes():
    ctx = MagicMock()
    ctx.vector_store = MagicMock()
    ctx.schema_store = MagicMock()
    ctx.schema_store.register_snapshot.return_value = ("v1", ["v0"])
    ctx.config_manager = MagicMock()
    ctx.config_manager.get_example_questions.return_value = ["q1"]

    adapter = MagicMock()
    adapter.datasource_id = "ds1"
    snapshot = make_snapshot()
    adapter.fetch_schema_snapshot.return_value = snapshot

    chunk = MagicMock()
    chunk.type = "schema.datasource"
    ctx.vector_store.refresh_schema_chunks.return_value = {"schema.datasource": 1}

    with patch("nl2sql.indexing.orchestrator.SchemaChunkBuilder") as builder_cls:
        builder = builder_cls.return_value
        builder.build.return_value = [chunk]

        orchestrator = IndexingOrchestrator(ctx)
        stats = orchestrator.index_datasource(adapter)

    ctx.schema_store.register_snapshot.assert_called_once_with(snapshot)
    ctx.config_manager.get_example_questions.assert_called_once_with("ds1")
    builder_cls.assert_called_once_with(
        ds_id="ds1",
        schema_snapshot=snapshot,
        schema_version="v1",
        questions=["q1"],
    )
    ctx.vector_store.refresh_schema_chunks.assert_called_once_with(
        datasource_id="ds1",
        schema_version="v1",
        chunks=[chunk],
        evicted_versions=["v0"],
    )
    assert stats["schema.datasource"] == 1
