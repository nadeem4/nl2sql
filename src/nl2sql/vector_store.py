from __future__ import annotations

import json
from typing import List, Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from sqlalchemy import inspect, Engine

from nl2sql.settings import settings
from nl2sql.embeddings import EmbeddingService

class SchemaVectorStore:
    """
    Manages vector indexing and retrieval of database schema information.

    Uses ChromaDB to store embeddings of table schemas (columns, comments, foreign keys)
    to allow semantic search for relevant tables based on user queries.
    """

    def __init__(self, collection_name: str = "schema_store", embeddings: Optional[Embeddings] = None, persist_directory: str = "./chroma_db"):
        """
        Initializes the SchemaVectorStore.

        Args:
            collection_name: Name of the ChromaDB collection.
            embeddings: Embedding model to use (defaults to EmbeddingService).
            persist_directory: Directory to persist the vector store.
        """
        self.collection_name = collection_name
        self.embeddings = embeddings or EmbeddingService.get_embeddings()
        self.persist_directory = persist_directory
        self.vectorstore = Chroma(
            collection_name=collection_name,
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )

    def is_empty(self) -> bool:
        """
        Checks if the vector store collection is empty.

        Returns:
            True if empty, False otherwise.
        """
        try:
            # Access the underlying Chroma collection to get count
            return self.vectorstore._collection.count() == 0
        except Exception:
            # If collection doesn't exist or error, assume empty
            return True

    def clear(self):
        """Clears the schema collection."""
        try:
            self.vectorstore.delete_collection()
            self.vectorstore = Chroma(
                collection_name=self.collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_directory
            )
        except Exception:
            pass

    def index_schema(self, engine: Engine, datasource_id: str):
        """
        Introspects the database and indexes table schemas into the vector store.

        Args:
            engine: SQLAlchemy engine for the database to index.
            datasource_id: ID of the datasource (added to metadata).
        """
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        documents = []

        for table in tables:
            columns = inspector.get_columns(table)
            # Create a rich text representation of the table
            col_desc = ", ".join([f"{col['name']} ({col['type']})" for col in columns])
            
            # Get foreign keys for context
            fks = inspector.get_foreign_keys(table)
            fk_desc = ""
            if fks:
                fk_list = []
                for fk in fks:
                    ref_table = fk.get("referred_table")
                    constrained_cols = fk.get("constrained_columns")
                    if ref_table and constrained_cols:
                        fk_list.append(f"-> {ref_table} ({', '.join(constrained_cols)})")
                if fk_list:
                    fk_desc = f" Foreign Keys: {'; '.join(fk_list)}."

            # Get table comment if available
            try:
                table_comment = inspector.get_table_comment(table)
                comment_text = table_comment.get("text") if table_comment else None
                comment_desc = f" Comment: {comment_text}." if comment_text else ""
            except Exception:
                comment_desc = ""

            content = f"Table: {table}.{comment_desc} Columns: {col_desc}.{fk_desc}"
            
            doc = Document(
                page_content=content,
                metadata={"table_name": table, "datasource_id": datasource_id}
            )
            documents.append(doc)

        if documents:
            self.vectorstore.add_documents(documents)

    def retrieve(self, query: str, k: int = 5) -> List[str]:
        """
        Retrieves relevant table names based on a natural language query.

        Args:
            query: The user's query string.
            k: Number of relevant tables to retrieve.

        Returns:
            List of table names.
        """
        docs = self.vectorstore.similarity_search(query, k=k)
        return [doc.metadata["table_name"] for doc in docs]
