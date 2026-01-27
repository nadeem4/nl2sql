import pytest
from unittest.mock import MagicMock, patch

from nl2sql.indexing.vector_store import VectorStore
from nl2sql.indexing.models import DatasourceChunk, TableChunk, TableRef


@pytest.fixture
def mock_chroma():
    """Provides a mocked Chroma client."""
    with patch("nl2sql.indexing.vector_store.Chroma") as mock:
        yield mock.return_value


@pytest.fixture
def store(mock_chroma):
    """Provides a VectorStore instance with mocked dependencies."""
    mock_embeddings = MagicMock()
    return VectorStore(embeddings=mock_embeddings)


def test_refresh_schema_chunks_indexes_and_stats(store, mock_chroma):
    table_ref = TableRef(schema_name="public", table_name="users")
    chunks = [
        DatasourceChunk(
            id="schema.datasource:ds1:v1",
            datasource_id="ds1",
            description="Test datasource",
            domains=[],
            schema_version="v1",
            examples=[],
        ),
        TableChunk(
            id="schema.table:public.users:v1",
            datasource_id="ds1",
            table=table_ref,
            description="Users table",
            primary_key=["id"],
            foreign_keys=[],
            row_count=10,
            schema_version="v1",
        ),
    ]

    stats = store.refresh_schema_chunks(
        datasource_id="ds1",
        schema_version="v1",
        chunks=chunks,
        evicted_versions=[],
    )

    assert mock_chroma.add_documents.called
    added_docs = mock_chroma.add_documents.call_args[0][0]
    assert len(added_docs) == 2
    assert stats["schema.datasource"] == 1
    assert stats["schema.table"] == 1


def test_refresh_schema_chunks_deletes_evicted_versions(store, mock_chroma):
    chunks = []
    store.refresh_schema_chunks(
        datasource_id="ds1",
        schema_version="v2",
        chunks=chunks,
        evicted_versions=["v1"],
    )

    assert mock_chroma._collection.delete.called


def test_is_empty_resilience(store, mock_chroma):
    """Verifies that is_empty() safely returns True if the underlying store is uninitialized or errors."""
    store.vectorstore = None
    assert store.is_empty() is True

    store.vectorstore = mock_chroma
    mock_chroma._collection.count.side_effect = Exception("DB Lock")
    assert store.is_empty() is True


def test_retrieve_planning_context_filters_by_table(store, mock_chroma):
    mock_chroma.max_marginal_relevance_search.return_value = []
    tables = ["public.users", "public.orders"]

    store.retrieve_planning_context("orders by user", "ds1", tables, k=4)

    _, kwargs = mock_chroma.max_marginal_relevance_search.call_args
    filt = kwargs["filter"]
    assert filt["datasource_id"] == "ds1"
    assert filt["type"]["$in"] == ["schema.column", "schema.relationship"]
    assert filt["table"]["$in"] == tables
