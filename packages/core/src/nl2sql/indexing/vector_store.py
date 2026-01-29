from __future__ import annotations

from typing import List, Optional, Dict, Any

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from nl2sql.indexing.embeddings import EmbeddingService
from nl2sql.common.logger import get_logger
from .models import BaseChunk

logger = get_logger(__name__)


class VectorStore:
    """
    Vector store for NL2SQL orchestration.

    This store indexes schema chunks and provides staged retrieval
    for datasource routing, schema grounding, and planning context.
    """

    def __init__(
        self,
        collection_name: str,
        persist_directory: str,
        embeddings: Optional[Embeddings] = None,
    ):
        """
        Initializes the vector store.

        Args:
            collection_name: Name of the Chroma collection.
            persist_directory: Directory used for persistence.
            embeddings: Embedding implementation to use.
        """
        self.collection_name = collection_name
        self.embeddings = embeddings or EmbeddingService.get_embeddings()
        self.persist_directory = persist_directory
        self._initialize_vector_store()

    def _initialize_vector_store(self) -> None:
        """
        Initializes the underlying Chroma vector store.
        """
        self.vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory,
        )

    def is_empty(self) -> bool:
        """
        Checks whether the vector store is empty.

        Returns:
            True if the store contains no documents.
        """
        try:
            return self.vectorstore._collection.count() == 0
        except Exception as exc:
            logger.error(f"Failed to check vector store state: {exc}")
            return True

    def clear(self) -> None:
        """
        Deletes the entire vector collection.
        """
        try:
            self.vectorstore.delete_collection()
            self._initialize_vector_store()
        except Exception as exc:
            logger.error(f"Failed to clear vector store: {exc}")

    def delete_documents(self, filter: Dict[str, Any]) -> None:
        """
        Deletes documents matching a metadata filter.

        Args:
            filter: Metadata filter used for deletion.
        """
        try:
            where = (
                {"$and": [{k: v} for k, v in filter.items()]}
                if len(filter) > 1
                else filter
            )
            self.vectorstore._collection.delete(where=where)
        except Exception as exc:
            logger.error(f"Failed to delete documents: {exc}")

    def refresh_schema_chunks(
        self,
        datasource_id: str,
        schema_version: str,
        chunks: List[BaseChunk],
        evicted_versions: List[str],
    ) -> Dict[str, int]:
        """
        Indexes schema chunks for a datasource and evicts old versions.

        Args:
            datasource_id: Datasource identifier.
            schema_version: Active schema version.
            chunks: Schema chunks to index.
            evicted_versions: Schema versions to remove.

        Returns:
            Indexing statistics by chunk type.
        """
        self._delete_evicted_versions(datasource_id, evicted_versions)

        self.delete_documents(
            {
                "datasource_id": datasource_id,
                "schema_version": schema_version,
            }
        )

        documents = self._prepare_chunk_documents(chunks)

        if documents:
            self.vectorstore.add_documents(documents)

        stats: Dict[str, int] = {}
        for chunk in chunks:
            stats[chunk.type] = stats.get(chunk.type, 0) + 1

        return stats

    def _delete_evicted_versions(
        self,
        datasource_id: str,
        evicted_versions: List[str],
    ) -> None:
        """
        Deletes all documents belonging to evicted schema versions.

        Args:
            datasource_id: Datasource identifier.
            evicted_versions: Schema versions to remove.
        """
        for version in evicted_versions:
            self.delete_documents(
                {
                    "datasource_id": datasource_id,
                    "schema_version": version,
                }
            )

    def _prepare_chunk_documents(
        self,
        chunks: List[BaseChunk],
    ) -> List[Document]:
        """
        Converts schema chunks into vector documents.

        Args:
            chunks: Schema chunks to convert.

        Returns:
            List of vector documents.
        """
        return [
            Document(
                page_content=chunk.get_page_content(),
                metadata=chunk.get_metadata(),
            )
            for chunk in chunks
        ]

    def retrieve_datasource_candidates(
        self,
        query: str,
        k: int = 3,
    ) -> List[Document]:
        """
        Retrieves candidate datasources for a user query.

        Args:
            query: User query.
            k: Number of datasource candidates to retrieve.

        Returns:
            Retrieved datasource documents.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        @VECTOR_BREAKER
        def _execute():
            return self.vectorstore.max_marginal_relevance_search(
                query,
                k=k,
                fetch_k=k * 4,
                lambda_mult=0.7,
                filter={"type": "schema.datasource"},
            )

        return _execute()

    def retrieve_schema_context(
        self,
        query: str,
        datasource_id: str,
        k: int = 8,
    ) -> List[Document]:
        """
        Retrieves schema-level context for a datasource.

        Args:
            query: User query.
            datasource_id: Selected datasource identifier.
            k: Number of schema documents to retrieve.

        Returns:
            Retrieved schema documents.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        @VECTOR_BREAKER
        def _execute():
            return self.vectorstore.max_marginal_relevance_search(
                query,
                k=k,
                fetch_k=k * 4,
                lambda_mult=0.7,
                filter={
                    "$and": [
                        {"datasource_id": datasource_id},
                        {"type": {"$in": ["schema.table", "schema.metric"]}},
                    ]
                },
            )

        return _execute()

    def retrieve_column_candidates(
        self,
        query: str,
        datasource_id: str,
        k: int = 8,
    ) -> List[Document]:
        """
        Retrieves candidate column documents for a datasource.

        Args:
            query: User query.
            datasource_id: Selected datasource identifier.
            k: Number of column documents to retrieve.

        Returns:
            Retrieved column documents.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        @VECTOR_BREAKER
        def _execute():
            return self.vectorstore.max_marginal_relevance_search(
                query,
                k=k,
                fetch_k=k * 4,
                lambda_mult=0.7,
                filter={
                    "$and": [
                        {"datasource_id": datasource_id},
                        {"type": "schema.column"},
                    ]
                },
            )

        return _execute()

    def retrieve_planning_context(
        self,
        query: str,
        datasource_id: str,
        tables: List[str],
        k: int = 12,
    ) -> List[Document]:
        """
        Retrieves planning-level context for selected tables.

        Args:
            query: User query.
            datasource_id: Selected datasource identifier.
            tables: Fully qualified table names.
            k: Number of planning documents to retrieve.

        Returns:
            Retrieved planning documents.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        @VECTOR_BREAKER
        def _execute():
            return self.vectorstore.max_marginal_relevance_search(
                query,
                k=k,
                fetch_k=k * 4,
                lambda_mult=0.7,
                filter={
                    "$and": [
                        {"datasource_id": datasource_id},
                        {"type": {"$in": ["schema.column", "schema.relationship"]}},
                        {"table": {"$in": tables}},
                    ]
                },
            )

        return _execute()
