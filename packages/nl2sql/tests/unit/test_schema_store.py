from nl2sql.schema import InMemorySchemaStore
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


def _multi_fk_contract() -> SchemaContract:
    """A table with two foreign keys, as Chinook's InvoiceLine and Track have."""
    from nl2sql_adapter_sdk.schema import ForeignKeyContract

    line = TableRef(schema_name="main", table_name="InvoiceLine")
    invoice = TableRef(schema_name="main", table_name="Invoice")
    track = TableRef(schema_name="main", table_name="Track")
    return SchemaContract(
        datasource_id="chinook",
        engine_type="sqlite",
        tables={
            line.full_name: TableContract(
                table=line,
                columns={"InvoiceLineId": ColumnContract(name="InvoiceLineId", data_type="INTEGER")},
                foreign_keys=[
                    ForeignKeyContract(constrained_columns=["TrackId"], referred_table=track,
                                       referred_columns=["TrackId"]),
                    ForeignKeyContract(constrained_columns=["InvoiceId"], referred_table=invoice,
                                       referred_columns=["InvoiceId"]),
                ],
            )
        },
    )


def test_fingerprint_handles_a_table_with_several_foreign_keys():
    """Sorting foreign keys must not compare TableRef models directly.

    TableRef has no ordering, so a table with two or more foreign keys raised
    `TypeError: '<' not supported between instances of 'TableRef'` and no
    snapshot could be registered at all -- `nl2sql demo` died on Chinook.
    """
    from nl2sql.schema.protocol import generate_schema_fingerprint

    contract = _multi_fk_contract()
    fingerprint = generate_schema_fingerprint(contract)
    assert fingerprint

    # The fingerprint is order-independent: the same foreign keys declared the
    # other way round must hash identically.
    table = contract.tables["[main].[InvoiceLine]"]
    reversed_contract = contract.model_copy(
        update={
            "tables": {
                "[main].[InvoiceLine]": table.model_copy(
                    update={"foreign_keys": list(reversed(table.foreign_keys))}
                )
            }
        }
    )
    assert generate_schema_fingerprint(reversed_contract) == fingerprint
