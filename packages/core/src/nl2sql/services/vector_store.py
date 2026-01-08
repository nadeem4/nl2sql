from __future__ import annotations

import json
from typing import List, Optional, Union

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from nl2sql.common.settings import settings
from nl2sql.services.embeddings import EmbeddingService
from nl2sql_adapter_sdk import SchemaMetadata, Table, Column, DatasourceAdapter

from nl2sql.common.logger import get_logger

logger = get_logger(__name__)


class OrchestratorVectorStore:
    """
    Manages vector indexing and retrieval for the Orchestrator Node.
    
    Stores:
    1. Table Schemas (Columns, FKs, Comments) -> L1 Routing
    2. Example Questions -> L2 Routing
    """

    def _initialize_vector_store(self):
        """Initializes the Chroma vector store client with the configured settings."""
        self.vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )

    def __init__(self, collection_name: str = "nl2sql_store", embeddings: Optional[Embeddings] = None, persist_directory: str = "./chroma_db"):
        """
        Initializes the OrchestratorVectorStore.

        Args:
            collection_name: Name of the ChromaDB collection.
            embeddings: Embedding model to use (defaults to EmbeddingService).
            persist_directory: Directory to persist the vector store.
        """
        self.collection_name = collection_name
        self.embeddings = embeddings or EmbeddingService.get_embeddings()
        self.persist_directory = persist_directory
        self._initialize_vector_store()

    def is_empty(self) -> bool:
        """
        Checks if the vector store collection is empty.

        Returns:
            True if empty, False otherwise.
        """
        if not self.vectorstore:
            return True
        try:
            return self.vectorstore._collection.count() == 0
        except Exception as e:
            logger.error(f"Failed to check if vector store is empty: {e}")
            return True

    def clear(self):
        """Clears the schema collection."""
        try:
            self.vectorstore.delete_collection()
            self._initialize_vector_store()
        except Exception as e:
            logger.error(f"Failed to clear vector store: {e}")


    def index_schema(self, adapter: DatasourceAdapter, datasource_id: str):
        """
        Introspects the database and indexes table schemas into the vector store.
        Applies Offline Aliasing:
        - Assigns context-independent aliases (e.g. {ds_id}_t1)
        - Prefixes column names with aliases (e.g. {ds_id}_t1.id)
        - Updates Foreign Keys to use aliased names.
        """
        schema_def = adapter.fetch_schema()
        documents = []

        alias_map = {}
        for i, table in enumerate(schema_def.tables):
            alias = f"{datasource_id}_t{i+1}"
            alias_map[table.name] = alias
            table.alias = alias

        for table in schema_def.tables:
            current_alias = table.alias 
            
            for col in table.columns:
                col.name = f"{current_alias}.{col.name}"

            for fk in table.foreign_keys:
                fk.constrained_columns = [f"{current_alias}.{c}" for c in fk.constrained_columns]
                
                target_alias = alias_map.get(fk.referred_table)
                if target_alias:
                    fk.referred_table = target_alias
                    fk.referred_columns = [f"{target_alias}.{c}" for c in fk.referred_columns]

            col_strs = []
            for col in table.columns:
                c_str = f"{col.name} ({col.type})"
                
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
            
            pks = [col.name for col in table.columns if col.is_primary_key]
            pk_desc = f" Primary Key: {', '.join(pks)}." if pks else ""

            fk_strs = []
            for fk in table.foreign_keys:
                cols_str = ",".join(fk.referred_columns)
                table_prefix = f"{fk.referred_table}."
                
                if fk.referred_columns and fk.referred_columns[0].startswith(table_prefix):
                    ref = cols_str
                else:
                    ref = f"{fk.referred_table}.{cols_str}"

                src = f"{','.join(fk.constrained_columns)}"
                fk_strs.append(f"{src} -> {ref}")
            fk_desc = f" Foreign Keys: {'; '.join(fk_strs)}." if fk_strs else ""

            comment_desc = f" Comment: {table.description}." if table.description else ""

            content = f"Table: {table.name} (Alias: {current_alias}).{comment_desc} Columns: {col_desc}.{pk_desc}{fk_desc}"
            
            doc = Document(
                page_content=content,
                metadata={
                    "table_name": table.name, 
                    "datasource_id": datasource_id, 
                    "type": "table",
                    "schema_json": table.model_dump_json()
                }
            )
            documents.append(doc)

        if documents:
            self.vectorstore.add_documents(documents)
            
        return {
            "tables": len(schema_def.tables),
            "columns": sum(len(t.columns) for t in schema_def.tables),
            "fks": sum(len(t.foreign_keys) for t in schema_def.tables)
        }

    def retrieve_table_names(self, query: str, k: int = 5, datasource_id: Optional[Union[str, List[str]]] = None) -> List[str]:
        """
        Retrieves relevant table names based on a natural language query.
        Used by SchemaNode to narrow down table selection.

        Args:
            query: The user's query string.
            k: Number of relevant tables to retrieve.
            datasource_id: Optional datasource ID(s) to filter by.

        Returns:
            List of table names.
        """
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

    def index_examples(self, examples_path: str, llm_registry=None):
        """
        Indexes example questions from a YAML file to aid in routing.
        """
        import yaml
        import pathlib
        from nl2sql.pipeline.nodes.semantic.node import SemanticAnalysisNode
        
        path = pathlib.Path(examples_path)
        if not path.exists():
            return

        documents = []
        try:
            examples = yaml.safe_load(path.read_text()) or {}
            
            enricher = None
            if llm_registry:
                try:
                    enricher = SemanticAnalysisNode(llm_registry.semantic_llm())
                except Exception as e:
                    logger.warning(f"Could not load SemanticNode: {e}")

            for ds_id, questions in examples.items():
                print(f"Processing examples for {ds_id}...")
                docs_for_ds = self.prepare_examples_for_datasource(ds_id, questions, enricher)
                documents.extend(docs_for_ds)
            
            if documents:
                self.vectorstore.add_documents(documents)
            
            return len(documents)
                
        except Exception as e:
            print(f"Failed to load examples: {e}")
            return 0

    def prepare_examples_for_datasource(self, ds_id: str, questions: List[str], enricher=None) -> List[Document]:
        """Prepares example documents for a specific datasource."""
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
                    print(f"Warning: Enrichment failed for '{q}': {e}")
                    
            variants = list(set(variants))

            for v in variants:
                doc = Document(
                    page_content=v,
                    metadata={"datasource_id": ds_id, "type": "example", "original": q}
                )
                documents.append(doc)
        return documents

    def add_documents(self, documents: List[Document]):
        """Directly adds documents to the vector store."""
        if documents:
            self.vectorstore.add_documents(documents)

    def retrieve_routing_context(self, query: str, k: int = 5, datasource_id: Optional[List[str]] = None) -> List[Document]:
        """
        Retrieves routing context using Partitioned MMR (Maximal Marginal Relevance).
        
        Strategy:
        1. Fetch Top-K TABLES using MMR (to ensure diversity of tables).
        2. Fetch Top-K EXAMPLES using MMR (to ensure diversity of intents).
        3. Merge results to avoid "Dominant Intent" masking secondary datasources.
        """
        filter_base = None
        if datasource_id:
            filter_base = {"datasource_id": {"$in": datasource_id}}
                    
        
        filter_tables = filter_base.copy() if filter_base else {}
        filter_tables["type"] = "table"
        
        table_docs = self.vectorstore.max_marginal_relevance_search(
            query, k=k, fetch_k=k*4, lambda_mult=0.7, filter=filter_tables
        )
        
        filter_examples = filter_base.copy() if filter_base else {}
        filter_examples["type"] = "example"
        
        example_docs = self.vectorstore.max_marginal_relevance_search(
            query, k=k, fetch_k=k*4, lambda_mult=0.7, filter=filter_examples
        )
        
        return table_docs + example_docs
