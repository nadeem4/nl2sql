
import pytest
from unittest.mock import MagicMock, patch, ANY
from nl2sql.services.vector_store import OrchestratorVectorStore
from langchain_core.documents import Document
from nl2sql_adapter_sdk import DatasourceAdapter, SchemaMetadata, Table, Column, ForeignKey

@pytest.fixture
def mock_chroma():
    """Provides a mocked Chroma client."""
    with patch("nl2sql.services.vector_store.Chroma") as mock:
        yield mock.return_value

@pytest.fixture
def store(mock_chroma):
    """Provides an OrchestratorVectorStore instance with mocked dependencies."""
    mock_embeddings = MagicMock()
    return OrchestratorVectorStore(embeddings=mock_embeddings)

def test_index_schema_fk_aliasing(store, mock_chroma):
    """
    Verifies that schema indexing correctly applies aliases to tables and updates 
    Foreign Key references to point to these new aliases.
    
    Expected behavior:
    - Tables are renamed to {datasource_id}_t{index}
    - Foreign Keys in downstream tables are updated to reference the aliased table names.
    - Original table names are preserved in metadata.
    """
    mock_adapter = MagicMock(spec=DatasourceAdapter)
    mock_adapter.fetch_schema.return_value = SchemaMetadata(
        datasource_id="ds1",
        datasource_engine_type="sqlite",
        tables=[
            Table(name="users", columns=[Column(name="id", type="INT", is_primary_key=True)]),
            Table(
                name="orders", 
                columns=[Column(name="user_id", type="INT")],
                foreign_keys=[
                    ForeignKey(
                        constrained_columns=["user_id"],
                        referred_table="users",
                        referred_columns=["id"]
                    )
                ]
            )
        ]
    )
    
    store.refresh_schema(mock_adapter, datasource_id="test_ds")
    
    mock_chroma.add_documents.assert_called_once()
    docs = mock_chroma.add_documents.call_args[0][0]
    
    users_doc = next(d for d in docs if d.metadata["table_name"] == "users")
    orders_doc = next(d for d in docs if d.metadata["table_name"] == "orders")
    
    # Verify Aliases
    # users -> test_ds_t1
    # orders -> test_ds_t2
    assert "Alias: test_ds_t1" in users_doc.page_content
    assert "Alias: test_ds_t2" in orders_doc.page_content
    
    # Verify Metadata JSON has original names
    assert users_doc.metadata["table_name"] == "users"
    
    # Verify FK in Orders points to Aliased Users
    # Expected: foreign keys should mention test_ds_t1.id
    assert "test_ds_t1.id" in orders_doc.page_content
    assert "test_ds_t2.user_id" in orders_doc.page_content 
    
    # Verify Correct String Formatting (No double aliasing)
    # Correct: test_ds_t2.user_id -> test_ds_t1.id
    # Buggy:   test_ds_t2.user_id -> test_ds_t1.test_ds_t1.id
    assert "test_ds_t2.user_id -> test_ds_t1.id" in orders_doc.page_content
    assert "test_ds_t1.test_ds_t1.id" not in orders_doc.page_content 

def test_index_schema_orphaned_fk(store, mock_chroma):
    """
    Verifies resilience when a Foreign Key refers to a table that does not exist 
    in the fetched schema (orphaned FK). The system should not crash.
    """
    mock_adapter = MagicMock(spec=DatasourceAdapter)
    mock_adapter.fetch_schema.return_value = SchemaMetadata(
        datasource_id="ds1",
        datasource_engine_type="sqlite",
        tables=[
            Table(
                name="orders", 
                columns=[Column(name="user_id", type="INT")],
                foreign_keys=[
                    ForeignKey(
                        constrained_columns=["user_id"],
                        referred_table="ghost_users",
                        referred_columns=["id"]
                    )
                ]
            )
        ]
    )
    
    store.refresh_schema(mock_adapter, datasource_id="test_ds")
    
    docs = mock_chroma.add_documents.call_args[0][0]
    orders_doc = docs[0]
    
    assert "ghost_users" in orders_doc.page_content

def test_retrieve_filtering_single(store, mock_chroma):
    """Verifies that retrieval correctly filters by a single datasource ID."""
    mock_chroma.similarity_search.return_value = []
    store.retrieve_table_names("q", datasource_id="ds1")
    
    mock_chroma.similarity_search.assert_called_with(
        "q", k=5, filter={"datasource_id": "ds1"}
    )

def test_retrieve_filtering_list(store, mock_chroma):
    """Verifies that retrieval correctly filters by a list of datasource IDs using the $in operator."""
    mock_chroma.similarity_search.return_value = []
    store.retrieve_table_names("q", datasource_id=["a", "b"])
    
    mock_chroma.similarity_search.assert_called_with(
        "q", k=5, filter={"datasource_id": {"$in": ["a", "b"]}}
    )

def test_retrieve_no_filter(store, mock_chroma):
    """Verifies that retrieval works correctly without any datasource filter."""
    mock_chroma.similarity_search.return_value = []
    store.retrieve_table_names("q")
    
    mock_chroma.similarity_search.assert_called_with(
        "q", k=5, filter=None
    )

def test_refresh_examples_enrichment_failure(store, mock_chroma):
    """
    Verifies system resilience when the LLM enrichment step fails (e.g. API error).
    The system should fallback to indexing the original question rather than crashing.
    """
    mock_enricher = MagicMock()
    mock_enricher.invoke.side_effect = Exception("API Down")
    
    examples = ["How many users?"]
    
    store.refresh_examples(datasource_id="ds1", examples=examples, enricher=mock_enricher)
        
    mock_chroma.add_documents.assert_called()
    docs = mock_chroma.add_documents.call_args[0][0]
    assert len(docs) == 1
    assert docs[0].page_content == "How many users?"

def test_is_empty_resilience(store, mock_chroma):
    """Verifies that is_empty() safely returns True if the underlying store is uninitialized or errors."""
    store.vectorstore = None
    assert store.is_empty() is True
    
    store.vectorstore = mock_chroma
    mock_chroma._collection.count.side_effect = Exception("DB Lock")
    assert store.is_empty() is True
