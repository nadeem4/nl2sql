"""The schema's structural fingerprint (``nl2sql.schema.fingerprint``)."""
from nl2sql_sdk.schema import ColumnContract, SchemaContract, TableContract, TableRef


def _multi_fk_contract() -> SchemaContract:
    """A table with two foreign keys, as Chinook's InvoiceLine and Track have."""
    from nl2sql_sdk.schema import ForeignKeyContract

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
    from nl2sql.schema.fingerprint import generate_schema_fingerprint

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
