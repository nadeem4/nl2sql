from nl2sql.schema import (
    InMemorySchemaStore,
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


def test_register_and_get_latest_snapshot():
    store = InMemorySchemaStore(max_versions=3)
    snap1 = make_snapshot(table_name="users")
    snap2 = make_snapshot(table_name="orders")

    v1, evicted1 = store.register_snapshot(snap1)
    v2, evicted2 = store.register_snapshot(snap2)

    assert evicted1 == []
    assert evicted2 == []
    assert store.list_versions("ds1") == [v1, v2]
    assert store.get_latest_version("ds1") == v2

    latest = store.get_latest_snapshot("ds1")
    assert latest is not None
    assert "[public].[orders]" in latest.contract.tables

    loaded = store.get_snapshot("ds1", v1)
    assert loaded is not None
    assert "[public].[users]" in loaded.contract.tables


def test_eviction_respects_max_versions():
    store = InMemorySchemaStore(max_versions=1)
    snap1 = make_snapshot(table_name="users")
    snap2 = make_snapshot(table_name="orders")

    v1, _ = store.register_snapshot(snap1)
    v2, evicted = store.register_snapshot(snap2)

    assert v1 in evicted
    assert store.list_versions("ds1") == [v2]
    assert store.get_snapshot("ds1", v1) is None


def test_per_table_access_returns_contract_and_metadata():
    store = InMemorySchemaStore(max_versions=3)
    snapshot = make_snapshot(table_name="users")
    version, _ = store.register_snapshot(snapshot)
    table_key = "[public].[users]"

    table_contract = store.get_table_contract("ds1", version, table_key)
    table_metadata = store.get_table_metadata("ds1", version, table_key)

    assert table_contract is not None
    assert table_contract.table.table_name == "users"
    assert table_metadata is not None
    assert table_metadata.row_count == 10
