
import pytest
from unittest.mock import MagicMock, patch
from nl2sql.services.vector_store import OrchestratorVectorStore
from langchain_core.documents import Document
from nl2sql_adapter_sdk import DatasourceAdapter, SchemaMetadata, Table, Column

def test_index_schema():
    """Test indexing schema into vector store."""
    mock_embeddings = MagicMock()
    mock_chroma = MagicMock()
    
    with patch("nl2sql.services.vector_store.Chroma", return_value=mock_chroma):
        
        # Mock Adapter
        mock_adapter = MagicMock(spec=DatasourceAdapter)
        mock_adapter.fetch_schema.return_value = SchemaMetadata(
            datasource_id="test_ds",
            tables=[
                Table(
                    name="table1",
                    columns=[Column(name="col1", type="INTEGER")]
                )
            ]
        )
        
        store = OrchestratorVectorStore(embeddings=mock_embeddings)
        store.index_schema(mock_adapter, datasource_id="test_ds")
        
        # Verify documents added
        mock_chroma.add_documents.assert_called_once()
        docs = mock_chroma.add_documents.call_args[0][0]
        assert len(docs) == 1
        assert "table1" in docs[0].page_content
        assert docs[0].metadata["table_name"] == "table1"
        assert docs[0].metadata["datasource_id"] == "test_ds"

def test_retrieve():
    """Test retrieving tables from vector store."""
    mock_chroma = MagicMock()
    mock_embeddings = MagicMock()
    
    with patch("nl2sql.services.vector_store.Chroma", return_value=mock_chroma):
        
        # Pass mock embeddings to avoid calling EmbeddingService.get_embeddings()
        store = OrchestratorVectorStore(embeddings=mock_embeddings)
        mock_chroma.similarity_search.return_value = [
            Document(page_content="...", metadata={"table_name": "table1"})
        ]
        
        results = store.retrieve_table_names("query")
        assert results == ["table1"]
