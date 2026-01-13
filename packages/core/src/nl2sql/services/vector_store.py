from __future__ import annotations

import json
from typing import List, Optional, Union, Dict, Any
from concurrent.futures import ProcessPoolExecutor, TimeoutError

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from nl2sql.common.settings import settings
from nl2sql.services.embeddings import EmbeddingService
from nl2sql_adapter_sdk import SchemaMetadata, Table, Column, DatasourceAdapter

from nl2sql.common.logger import get_logger
from nl2sql.common.sandbox import get_indexing_pool
from nl2sql.datasources.discovery import discover_adapters

logger = get_logger(__name__)


from nl2sql.common.contracts import ExecutionRequest, ExecutionResult

def _fetch_schema_in_process(request: ExecutionRequest) -> ExecutionResult:
    """Fetches schema in a separate process to isolate crashes."""
    available = discover_adapters()
    if request.engine_type not in available:
        return ExecutionResult(success=False, error=f"Unknown engine: {request.engine_type}")
    
    try:
        adapter_cls = available[request.engine_type]
        adapter = adapter_cls(
            datasource_id=request.datasource_id,
            datasource_engine_type=request.engine_type,
            connection_args=request.connection_args
        )
        schema = adapter.fetch_schema()
        return ExecutionResult(success=True, data=schema)
    except Exception as e:
        return ExecutionResult(success=False, error=str(e))


class OrchestratorVectorStore:
    """Manages vector indexing and retrieval for the Orchestrator Node.

    Indexes table schemas (for L1 routing) and example questions (for L2 routing)
    into a ChromaDB vector store.
    """

    def __init__(
        self,
        collection_name: str = "nl2sql_store",
        embeddings: Optional[Embeddings] = None,
        persist_directory: str = "./chroma_db",
    ):
        """Initializes the OrchestratorVectorStore.

        Args:
            collection_name: Name of the ChromaDB collection.
            embeddings: Embedding model to use. Defaults to EmbeddingService.
            persist_directory: Directory to persist the vector store.
        """
        self.collection_name = collection_name
        self.embeddings = embeddings or EmbeddingService.get_embeddings()
        self.persist_directory = persist_directory
        self._initialize_vector_store()

    def _initialize_vector_store(self):
        """Initializes the Chroma vector store client."""
        self.vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory,
        )

    def is_empty(self) -> bool:
        """Checks if the vector store collection is empty.

        Returns:
            bool: True if empty, False otherwise.
        """
        if not self.vectorstore:
            return True
        try:
            return self.vectorstore._collection.count() == 0
        except Exception as e:
            logger.error(f"Failed to check if vector store is empty: {e}")
            return True

    def clear(self):
        """Clears the entire schema collection."""
        try:
            self.vectorstore.delete_collection()
            self._initialize_vector_store()
        except Exception as e:
            logger.error(f"Failed to clear vector store: {e}")

    def delete_documents(self, filter: Dict[str, Any]):
        """Deletes documents matching the given metadata filter.

        Args:
            filter: A dictionary of metadata filters (e.g., {"datasource_id": "sales"}).
        """
        try:
            if self.vectorstore._collection:
                where_clause = filter
                if len(filter) > 1:
                    where_clause = {"$and": [{k: v} for k, v in filter.items()]}
                
                self.vectorstore._collection.delete(where=where_clause)
        except Exception as e:
            logger.error(f"Failed to delete documents with filter {filter}: {e}")

    def refresh_schema(self, adapter: DatasourceAdapter, datasource_id: str) -> Dict[str, int]:
        """Refreshes the schema index for a specific datasource.

        This operation is idempotent. It first deletes all existing table documents
        for the given datasource_id, then fetches the fresh schema and indexes it.
        
        The schema fetching happens in a Sandboxed process (Indexing Pool) to preserve
        main process stability during heavy Introspection IO.

        Args:
            adapter (DatasourceAdapter): The DatasourceAdapter to fetch schema from.
            datasource_id (str): The ID of the datasource to refresh.

        Returns:
            Dict[str, int]: Stats containing counts of indexed tables, columns, and foreign keys.

        Raises:
        Raises:
             ValueError: If schema fetching fails in the sandbox.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        @VECTOR_BREAKER
        def _execute_refresh():
            self.delete_documents(filter={"datasource_id": datasource_id, "type": "table"})

            pool = get_indexing_pool()
                 
            request = ExecutionRequest(
                mode="fetch_schema",
                datasource_id=datasource_id,
                engine_type=adapter.datasource_engine_type,
                connection_args=adapter.connection_args
            )

            from nl2sql.common.sandbox import execute_in_sandbox
            result_contract = execute_in_sandbox(pool, _fetch_schema_in_process, request, timeout_sec=60)
             
            if not result_contract.success:
                if result_contract.metrics.get("is_crash"):
                    logger.critical(f"Schema indexing caused Sandbox Crash: {result_contract.error}")
                    raise ValueError(f"Schema Fetch CRASHED (Segfault/OOM): {result_contract.error}")
                else:
                    raise ValueError(f"Schema Fetch Failed: {result_contract.error}")

            schema_def = result_contract.data

            documents = []
            alias_map = {}

            for i, table_obj in enumerate(schema_def.tables):
                alias = f"{datasource_id}_t{i+1}"
                alias_map[table_obj.name] = alias
                table_obj.alias = alias

            for table_obj in schema_def.tables:
                current_alias = table_obj.alias

                col_strs = []
                for col in table_obj.columns:
                    c_str = f"{current_alias}.{col.name} ({col.type})"
                    if col.description:
                        c_str += f" '{col.description}'"
                    
                    if col.statistics:
                        stats = col.statistics
                        extras = []
                        if stats.sample_values:
                            formatted_samples = [str(v) for v in stats.sample_values[:5]]
                            extras.append(f"Samples: {formatted_samples}")
                        if stats.min_value is not None and stats.max_value is not None:
                            extras.append(f"Range: {stats.min_value}..{stats.max_value}")
                        if extras:
                            c_str += f" [{'; '.join(extras)}]"
                    col_strs.append(c_str)
                col_desc = ", ".join(col_strs)

                pks = [col.name for col in table_obj.columns if col.is_primary_key]
                pk_desc = f" Primary Key: {', '.join(pks)}." if pks else ""

                fk_strs = []
                for fk in table_obj.foreign_keys:
                    target_alias = alias_map.get(fk.referred_table, fk.referred_table)
                    src_cols = ",".join([f"{current_alias}.{c}" for c in fk.constrained_columns])
                    ref_cols = ",".join(fk.referred_columns)
                    fk_strs.append(f"{src_cols} -> {target_alias}.{ref_cols}")
                
                fk_desc = f" Foreign Keys: {'; '.join(fk_strs)}." if fk_strs else ""
                comment_desc = f" Comment: {table_obj.description}." if table_obj.description else ""

                content = (
                    f"Table: {table_obj.name} (Alias: {current_alias}).{comment_desc} "
                    f"Columns: {col_desc}.{pk_desc}{fk_desc}"
                )

                doc = Document(
                    page_content=content,
                    metadata={
                        "table_name": table_obj.name,
                        "datasource_id": datasource_id,
                        "type": "table",
                        "schema_json": table_obj.model_dump_json(),
                    },
                )
                documents.append(doc)

            if documents:
                self.vectorstore.add_documents(documents)

            return {
                "tables": len(schema_def.tables),
                "columns": sum(len(t.columns) for t in schema_def.tables),
                "fks": sum(len(t.foreign_keys) for t in schema_def.tables),
            }

        return _execute_refresh()

    def refresh_examples(self, datasource_id: str, examples: List[str], enricher: Any = None) -> int:
        """Refreshes the example questions index for a specific datasource.

        This operation is idempotent. It first deletes all existing example documents
        for the given datasource_id, then indexes the provided examples.

        Args:
            datasource_id: The ID of the datasource.
            examples: A list of example question strings.
            enricher: Optional SemanticAnalysisNode to enrich examples.

        Returns:
            int: The number of documents indexed.
        """
        self.delete_documents(filter={"datasource_id": datasource_id, "type": "example"})

        documents = self._prepare_examples_for_datasource(datasource_id, examples, enricher)
        if documents:
            self.vectorstore.add_documents(documents)
        
        return len(documents)

    def retrieve_table_names(
        self, query: str, k: int = 5, datasource_id: Optional[Union[str, List[str]]] = None
    ) -> List[str]:
        """Retrieves relevant table names based on a natural language query.

        Args:
            query: The user's query string.
            k: Number of relevant tables to retrieve.
            datasource_id: Optional datasource ID(s) to filter by.

        Returns:
            List[str]: List of table names.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        @VECTOR_BREAKER
        def _execute_search():
            filter_arg = None
            if datasource_id:
                if isinstance(datasource_id, str):
                    filter_arg = {"datasource_id": datasource_id}
                elif isinstance(datasource_id, list):
                    if len(datasource_id) == 1:
                        filter_arg = {"datasource_id": datasource_id[0]}
                    else:
                        filter_arg = {"datasource_id": {"$in": datasource_id}}

            docs = self.vectorstore.similarity_search(query, k=k, filter=filter_arg)
            return [doc.metadata.get("table_name", "Unknown") for doc in docs]

        return _execute_search()

    def _prepare_examples_for_datasource(
        self, ds_id: str, questions: List[str], enricher: Any = None
    ) -> List[Document]:
        """Prepares example documents for a specific datasource.

        Args:
            ds_id: Datasource ID.
            questions: List of questions.
            enricher: Optional enricher node.

        Returns:
            List[Document]: List of prepared documents.
        """
        documents = []
        for q in questions:
            variants = [q]
            if enricher:
                try:
                    analysis = enricher.invoke(q)
                    if analysis.canonical_query:
                        variants.append(analysis.canonical_query)
                    if analysis.keywords or analysis.synonyms:
                        meta_text = " ".join(analysis.keywords + analysis.synonyms)
                        variants.append(meta_text)
                except Exception as e:
                    logger.warning(f"Enrichment failed for '{q}': {e}")

            variants = list(set(variants))
            for v in variants:
                doc = Document(
                    page_content=v,
                    metadata={"datasource_id": ds_id, "type": "example", "original": q},
                )
                documents.append(doc)
        return documents

    def retrieve_routing_context(
        self, query: str, k: int = 5, datasource_id: Optional[List[str]] = None
    ) -> List[Document]:
        """Retrieves routing context using Partitioned MMR.

        Fetches both table and example context to aid in routing decisions.

        Args:
            query: The input query.
            k: Number of results to retrieve per type.
            datasource_id: Optional list of datasource IDs to filter.

        Returns:
            List[Document]: Combined list of relevant documents.
        """
        from nl2sql.common.resilience import VECTOR_BREAKER

        @VECTOR_BREAKER
        def _execute_mmr():
            filter_base = None
            if datasource_id:
                filter_base = {"datasource_id": {"$in": datasource_id}}

            filter_tables = filter_base.copy() if filter_base else {}
            filter_tables["type"] = "table"

            table_docs = self.vectorstore.max_marginal_relevance_search(
                query, k=k, fetch_k=k * 4, lambda_mult=0.7, filter=filter_tables
            )

            filter_examples = filter_base.copy() if filter_base else {}
            filter_examples["type"] = "example"

            example_docs = self.vectorstore.max_marginal_relevance_search(
                query, k=k, fetch_k=k * 4, lambda_mult=0.7, filter=filter_examples
            )

            return table_docs + example_docs

        return _execute_mmr()


