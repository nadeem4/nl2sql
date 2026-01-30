from nl2sql.indexing.models import TableChunk, TableRef, ColumnChunk, ColumnRef


def test_table_chunk_includes_columns_in_content():
    table_ref = TableRef(schema_name="public", table_name="users")
    chunk = TableChunk(
        id="schema.table:public.users:v1",
        datasource_id="ds1",
        table=table_ref,
        description="User records",
        primary_key=["id"],
        columns=["id", "email"],
        foreign_keys=[],
        row_count=10,
        schema_version="v1",
    )

    content = chunk.get_page_content()
    assert "Columns: id, email" in content


def test_column_chunk_includes_synonyms_in_content():
    column_ref = ColumnRef(schema_name="public", table_name="users", column_name="email")
    chunk = ColumnChunk(
        id="schema.column:public.users.email:v1",
        datasource_id="ds1",
        column=column_ref,
        dtype="text",
        description="Email address",
        column_stats={},
        synonyms=["email_address", "mail"],
        pii=True,
        schema_version="v1",
    )

    content = chunk.get_page_content()
    assert "Synonyms: email_address, mail" in content
