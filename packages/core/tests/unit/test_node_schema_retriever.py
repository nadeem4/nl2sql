from types import SimpleNamespace

from nl2sql.pipeline.nodes.schema_retriever.node import SchemaRetrieverNode
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql_adapter_sdk.schema import (
    SchemaSnapshot,
    SchemaContract,
    SchemaMetadata,
    TableContract,
    TableMetadata,
    TableRef,
    ColumnContract,
)


def test_schema_retriever_falls_back_to_schema_store():
    # Validates fallback logic because schema store is used without planning docs.
    # Arrange
    vector_store = SimpleNamespace(
        retrieve_schema_context=lambda *_a, **_k: [],
        retrieve_planning_context=lambda *_a, **_k: [],
    )
    table_ref = TableRef(schema_name="public", table_name="users")
    snapshot = SchemaSnapshot(
        contract=SchemaContract(
            datasource_id="ds1",
            engine_type="sqlite",
            tables={
                table_ref.full_name: TableContract(
                    table=table_ref,
                    columns={"id": ColumnContract(name="id", data_type="int", is_nullable=False, is_primary_key=True)},
                    foreign_keys=[],
                )
            },
        ),
        metadata=SchemaMetadata(
            datasource_id="ds1",
            engine_type="sqlite",
            tables={table_ref.full_name: TableMetadata(table=table_ref, row_count=1, columns={})},
        ),
    )
    schema_store = SimpleNamespace(get_latest_snapshot=lambda _id: snapshot)
    ctx = SimpleNamespace(vector_store=vector_store, schema_store=schema_store)
    node = SchemaRetrieverNode(ctx)

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="list users"),
    )

    # Act
    result = node(state)

    # Assert
    assert result["relevant_tables"]
    assert result["relevant_tables"][0].name == "users"


def test_schema_retriever_builds_tables_from_docs():
    # Validates planning docs usage because vector store should enrich schema context.
    # Arrange
    doc_table = SimpleNamespace()
    doc_table.metadata = {"table": "public.users"}
    doc_col = SimpleNamespace()
    doc_col.metadata = {
        "table": "public.users",
        "type": "schema.column",
        "column": "public.users.id",
        "dtype": "int",
    }
    doc_rel = SimpleNamespace()
    doc_rel.metadata = {
        "type": "schema.relationship",
        "from_table": "public.users",
        "to_table": "public.orders",
        "from_columns": ["id"],
        "to_columns": ["user_id"],
    }
    vector_store = SimpleNamespace(
        retrieve_schema_context=lambda *_a, **_k: [doc_table],
        retrieve_planning_context=lambda *_a, **_k: [doc_col, doc_rel],
    )
    ctx = SimpleNamespace(vector_store=vector_store, schema_store=SimpleNamespace())
    node = SchemaRetrieverNode(ctx)

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="list users"),
    )

    # Act
    result = node(state)

    # Assert
    tables = result["relevant_tables"]
    assert any(t.name == "users" for t in tables)
    user_cols = [c.name for c in next(t for t in tables if t.name == "users").columns]
    assert "id" in user_cols
