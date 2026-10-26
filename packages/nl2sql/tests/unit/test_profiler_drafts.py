"""The drafting helpers K3 moved out of the index (nl2sql/profiler/drafts.py), unchanged."""
from nl2sql.profiler.drafts import (
    SchemaEnrichment,
    DatasourceEnrichment,
    TableEnrichment,
    ColumnEnrichment,
    sanitize_enrichment,
)
from nl2sql_sdk.schema import (
    SchemaSnapshot,
    SchemaContract,
    SchemaMetadata,
    TableContract,
    TableMetadata,
    TableRef,
    ColumnContract,
)


def _build_snapshot():
    table_ref = TableRef(schema_name="public", table_name="users")
    contract = SchemaContract(
        datasource_id="ds1",
        engine_type="sqlite",
        tables={
            table_ref.full_name: TableContract(
                table=table_ref,
                columns={
                    "id": ColumnContract(name="id", data_type="int"),
                    "email": ColumnContract(name="email", data_type="text"),
                },
            )
        },
    )
    metadata = SchemaMetadata(
        datasource_id="ds1",
        engine_type="sqlite",
        tables={
            table_ref.full_name: TableMetadata(table=table_ref, columns={})
        },
    )
    return SchemaSnapshot(contract=contract, metadata=metadata)


def test_sanitize_enrichment_filters_unknowns_and_questions():
    snapshot = _build_snapshot()
    table_key = next(iter(snapshot.contract.tables.keys()))
    enrichment = SchemaEnrichment(
        datasource=DatasourceEnrichment(
            description="User data",
            domains=["crm"],
            sample_questions=["List users", "Show revenue by region"],
        ),
        tables={
            table_key: TableEnrichment(description="Users table"),
            "public.orders": TableEnrichment(description="Orders table"),
        },
        columns={
            f"{table_key}.email": ColumnEnrichment(
                description="Email address",
                synonyms=["email_address", "mail"],
            ),
            "public.orders.total": ColumnEnrichment(
                description="Total",
                synonyms=["amount"],
            ),
        },
    )

    sanitized = sanitize_enrichment(snapshot, enrichment, max_questions=100)

    assert "public.orders" not in sanitized.tables
    assert "public.orders.total" not in sanitized.columns
    assert sanitized.datasource.sample_questions == ["List users"]

