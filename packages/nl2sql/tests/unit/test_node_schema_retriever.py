from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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


USERS = TableRef(schema_name="public", table_name="users")
ORDERS = TableRef(schema_name="public", table_name="orders")


def _snapshot() -> SchemaSnapshot:
    """Snapshot the retriever resolves table/column contracts from."""
    return SchemaSnapshot(
        contract=SchemaContract(
            datasource_id="ds1",
            engine_type="sqlite",
            tables={
                USERS.full_name: TableContract(
                    table=USERS,
                    columns={"id": ColumnContract(name="id", data_type="int", is_nullable=False, is_primary_key=True)},
                    foreign_keys=[],
                ),
                ORDERS.full_name: TableContract(
                    table=ORDERS,
                    columns={
                        "order_id": ColumnContract(
                            name="order_id", data_type="int", is_nullable=False, is_primary_key=True
                        ),
                        "user_id": ColumnContract(name="user_id", data_type="int"),
                    },
                    foreign_keys=[],
                ),
            },
        ),
        metadata=SchemaMetadata(
            datasource_id="ds1",
            engine_type="sqlite",
            tables={
                USERS.full_name: TableMetadata(table=USERS, row_count=1, columns={}),
                ORDERS.full_name: TableMetadata(table=ORDERS, row_count=1, columns={}),
            },
        ),
    )


def _schema_store() -> SimpleNamespace:
    return SimpleNamespace(get_latest_snapshot=lambda _id: _snapshot())


def test_schema_retriever_falls_back_to_schema_store():
    # Validates fallback logic because schema store is used without planning docs.
    # Arrange
    vector_store = SimpleNamespace(
        retrieve_schema_context=lambda *_a, **_k: [],
        retrieve_planning_context=lambda *_a, **_k: [],
        retrieve_column_candidates=lambda *_a, **_k: [],
    )
    ctx = SimpleNamespace(vector_store=vector_store, schema_store=_schema_store())
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
    doc_table.metadata = {"table": USERS.full_name}
    doc_col = SimpleNamespace()
    doc_col.metadata = {
        "table": USERS.full_name,
        "type": "schema.column",
        "column": "id",
        "dtype": "int",
    }
    doc_rel = SimpleNamespace()
    doc_rel.metadata = {
        "type": "schema.relationship",
        "from_table": USERS.full_name,
        "to_table": ORDERS.full_name,
        "from_columns": ["id"],
        "to_columns": ["user_id"],
    }
    vector_store = SimpleNamespace(
        retrieve_schema_context=lambda *_a, **_k: [doc_table],
        retrieve_planning_context=lambda *_a, **_k: [doc_col, doc_rel],
        retrieve_column_candidates=lambda *_a, **_k: [],
    )
    ctx = SimpleNamespace(vector_store=vector_store, schema_store=_schema_store())
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


def test_schema_retriever_uses_column_fallback():
    # Validates column-first fallback when table docs are missing.
    doc_col = SimpleNamespace()
    doc_col.metadata = {
        "type": "schema.column",
        "column": "order_id",
        "table": ORDERS.full_name,
        "dtype": "int",
    }
    vector_store = SimpleNamespace(
        retrieve_schema_context=lambda *_a, **_k: [],
        retrieve_planning_context=lambda *_a, **_k: [doc_col],
        retrieve_column_candidates=lambda *_a, **_k: [doc_col],
    )
    ctx = SimpleNamespace(vector_store=vector_store, schema_store=_schema_store())
    node = SchemaRetrieverNode(ctx)

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="orders by id"),
    )

    result = node(state)

    tables = result["relevant_tables"]
    assert any(t.name == "orders" for t in tables)


@pytest.fixture
def retriever_with_snapshot():
    """Build a retriever over a snapshot with ``table_count`` tables."""

    def _factory(table_count: int):
        refs = [TableRef(schema_name="public", table_name=f"t{i}") for i in range(table_count)]
        snapshot = SchemaSnapshot(
            contract=SchemaContract(
                datasource_id="ds",
                engine_type="sqlite",
                tables={
                    ref.full_name: TableContract(
                        table=ref,
                        columns={
                            "id": ColumnContract(
                                name="id", data_type="int", is_nullable=False, is_primary_key=True
                            ),
                            "name": ColumnContract(name="name", data_type="text"),
                        },
                        foreign_keys=[],
                    )
                    for ref in refs
                },
            ),
            metadata=SchemaMetadata(
                datasource_id="ds",
                engine_type="sqlite",
                tables={ref.full_name: TableMetadata(table=ref, row_count=1, columns={}) for ref in refs},
            ),
        )
        vector_store = SimpleNamespace(
            retrieve_schema_context=MagicMock(return_value=[]),
            retrieve_planning_context=MagicMock(return_value=[]),
            retrieve_column_candidates=MagicMock(return_value=[]),
        )
        schema_store = SimpleNamespace(get_latest_snapshot=lambda _id: snapshot)
        ctx = SimpleNamespace(vector_store=vector_store, schema_store=schema_store)
        node = SchemaRetrieverNode(ctx)
        return node, vector_store, {ref.table_name for ref in refs}

    return _factory


def test_small_schema_bypasses_vector_retrieval(retriever_with_snapshot, monkeypatch):
    node, vector_store, snapshot_tables = retriever_with_snapshot(table_count=3)
    monkeypatch.setattr(
        "nl2sql.pipeline.nodes.schema_retriever.node.settings.schema_retrieval_full_snapshot_max_tables",
        15,
    )
    out = node(SubgraphExecutionState(trace_id="t", sub_query=SubQuery(id="sq1", datasource_id="ds", intent="q")))
    assert {t.name for t in out["relevant_tables"]} == snapshot_tables
    assert all(len(t.columns) > 0 for t in out["relevant_tables"])
    vector_store.retrieve_schema_context.assert_not_called()
