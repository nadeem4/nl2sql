from nl2sql.schema import SqliteSchemaStore
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


def test_sqlite_schema_store_round_trip(tmp_path):
    store = SqliteSchemaStore(path=tmp_path / "schema_store.db", max_versions=3)
    snapshot = _snapshot("users")
    version, evicted = store.register_snapshot(snapshot)

    assert evicted == []
    fetched = store.get_snapshot("ds1", version)
    assert fetched is not None
    table_name = "[public].[users]"
    assert table_name in fetched.contract.tables


def test_sqlite_schema_store_fingerprint_reuse(tmp_path):
    store = SqliteSchemaStore(path=tmp_path / "schema_store.db", max_versions=3)
    snapshot = _snapshot("users")

    version1, evicted1 = store.register_snapshot(snapshot)
    version2, evicted2 = store.register_snapshot(snapshot)

    assert version1 == version2
    assert evicted1 == []
    assert evicted2 == []


def test_sqlite_schema_store_eviction(tmp_path):
    store = SqliteSchemaStore(path=tmp_path / "schema_store.db", max_versions=2)
    v1, _ = store.register_snapshot(_snapshot("users"))
    v2, _ = store.register_snapshot(_snapshot("orders"))
    v3, evicted = store.register_snapshot(_snapshot("payments"))

    assert evicted == [v1]
    assert store.list_versions("ds1") == [v2, v3]


def test_sqlite_schema_store_table_accessors(tmp_path):
    store = SqliteSchemaStore(path=tmp_path / "schema_store.db", max_versions=3)
    snapshot = _snapshot("users")
    version, _ = store.register_snapshot(snapshot)
    table_key = "[public].[users]"

    assert store.get_table_contract("ds1", version, table_key) is not None
    assert store.get_table_metadata("ds1", version, table_key) is not None


def test_one_store_serves_many_threads_at_once(tmp_path):
    """The playground's first page load reads the schema and the index health
    on two threadpool workers at once, through the one store. A shared
    sqlite3 connection used from two threads at the same moment raised
    ``InterfaceError: bad parameter or other API misuse`` and ``/api/index``
    answered 500."""
    import threading

    # Arrange
    store = SqliteSchemaStore(path=tmp_path / "schema_store.db", max_versions=3)
    store.register_snapshot(_snapshot("users"))
    failures = []
    start = threading.Barrier(8)

    def reader():
        start.wait()
        try:
            for _ in range(300):
                assert store.get_latest_version("ds1")
                assert store.get_latest_snapshot("ds1") is not None
        except Exception as exc:  # noqa: BLE001 - collected and asserted below
            failures.append(repr(exc))

    # Act
    threads = [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Assert
    assert failures == []
