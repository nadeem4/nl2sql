from nl2sql.schema.store import InMemorySchemaStore
from nl2sql_adapter_sdk.schema import (
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
    TableRef,
    ColumnContract,
)


def _snapshot(table_name: str) -> SchemaSnapshot:
    table_ref = TableRef(schema_name="public", table_name=table_name)
    contract = SchemaContract(
        datasource_id="ds1",
        engine_type="postgres",
        tables={
            table_ref.full_name: TableContract(
                table=table_ref,
                columns={
                    "id": ColumnContract(
                        name="id", data_type="int", is_nullable=False, is_primary_key=True
                    )
                },
                foreign_keys=[],
            )
        },
    )
    metadata = SchemaMetadata(
        datasource_id="ds1",
        engine_type="postgres",
        tables={
            table_ref.full_name: TableMetadata(
                table=table_ref, row_count=10, columns={}
            )
        },
    )
    return SchemaSnapshot(contract=contract, metadata=metadata)


def test_schema_store_versions_and_latest_snapshot():
    # Validates schema evolution because multiple versions must be tracked.
    # Arrange
    store = InMemorySchemaStore(max_versions=3)
    snap1 = _snapshot("users")
    snap2 = _snapshot("orders")

    # Act
    v1, _ = store.register_snapshot(snap1)
    v2, _ = store.register_snapshot(snap2)

    # Assert
    assert store.list_versions("ds1") == [v1, v2]
    assert store.get_latest_version("ds1") == v2
    assert store.get_latest_snapshot("ds1").contract.tables
