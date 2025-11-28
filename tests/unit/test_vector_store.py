
import pytest
from unittest.mock import MagicMock, patch
from nl2sql.vector_store import SchemaVectorStore
from langchain_core.documents import Document

def test_index_schema():
    """Test indexing schema into vector store."""
    mock_embeddings = MagicMock()
    mock_chroma = MagicMock()
    
    with patch("nl2sql.vector_store.Chroma", return_value=mock_chroma), \
         patch("nl2sql.vector_store.OpenAIEmbeddings", return_value=mock_embeddings), \
         patch("nl2sql.vector_store.inspect") as mock_inspect:
        
        mock_engine = MagicMock()
        mock_inspector = mock_inspect.return_value
        mock_inspector.get_table_names.return_value = ["table1"]
        mock_inspector.get_columns.return_value = [{"name": "col1", "type": "INTEGER"}]
        mock_inspector.get_foreign_keys.return_value = []
        
        store = SchemaVectorStore(embeddings=mock_embeddings)
        store.index_schema(mock_engine)
        
        # Verify documents added
        mock_chroma.add_documents.assert_called_once()
        docs = mock_chroma.add_documents.call_args[0][0]
        assert len(docs) == 1
        assert "table1" in docs[0].page_content
        assert docs[0].metadata["table_name"] == "table1"

def test_retrieve():
    """Test retrieving tables from vector store."""
    mock_chroma = MagicMock()
    
    with patch("nl2sql.vector_store.Chroma", return_value=mock_chroma), \
         patch("nl2sql.vector_store.OpenAIEmbeddings"):
        
        store = SchemaVectorStore()
        mock_chroma.similarity_search.return_value = [
            Document(page_content="...", metadata={"table_name": "table1"})
        ]
        
        results = store.retrieve("query")
        assert results == ["table1"]
