from __future__ import annotations

import json
from typing import List, Optional, Union

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from sqlalchemy import inspect, Engine

from nl2sql.settings import settings
from nl2sql.embeddings import EmbeddingService

class OrchestratorVectorStore:
    """
    Manages vector indexing and retrieval for the Orchestrator Node.
    
    Stores:
    1. Table Schemas (Columns, FKs, Comments) -> L1 Routing
    2. Example Questions -> L2 Routing
    """

    def __init__(self, collection_name: str = "orchestrator_store", embeddings: Optional[Embeddings] = None, persist_directory: str = "./chroma_db"):
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
            col_desc = ", ".join([f"{col['name']} ({col['type']})" for col in columns])
            
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

            try:
                table_comment = inspector.get_table_comment(table)
                comment_text = table_comment.get("text") if table_comment else None
                comment_desc = f" Comment: {comment_text}." if comment_text else ""
            except Exception:
                comment_desc = ""

            content = f"Table: {table}.{comment_desc} Columns: {col_desc}.{fk_desc}"
            
            doc = Document(
                page_content=content,
                metadata={"table_name": table, "datasource_id": datasource_id, "type": "table"}
            )
            documents.append(doc)

        if documents:
            self.vectorstore.add_documents(documents)
            
        return tables

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

    def index_examples(self, examples_path: str, llm=None):
        """
        Indexes example questions from a YAML file to aid in routing.
        """
        import yaml
        import pathlib
        from nl2sql.agents import canonicalize_query, enrich_question
        
        path = pathlib.Path(examples_path)
        if not path.exists():
            return

        documents = []
        try:
            examples = yaml.safe_load(path.read_text()) or {}
            
            for ds_id, questions in examples.items():
                print(f"Processing examples for {ds_id}...")
                for q in questions:
                    variants = [q]
                    if llm:
                        try:
                            canonical_q = canonicalize_query(q, llm) 
                            variants.append(canonical_q)
                            
                            enrichments = enrich_question(q, llm)
                            variants.extend(enrichments)
                        except Exception as e:
                            print(f"Warning: Enrichment failed for '{q}': {e}")
                            
                    variants = list(set(variants))

                    for v in variants:
                        doc = Document(
                            page_content=v,
                            metadata={"datasource_id": ds_id, "type": "example", "original": q}
                        )
                        documents.append(doc)
            
            if documents:
                self.vectorstore.add_documents(documents)
                print(f"Indexed {len(documents)} example questions (with variants).")
                
        except Exception as e:
            print(f"Failed to load examples: {e}")

    def retrieve_routing_context(self, query: str, k: int = 5, datasource_id: Optional[Union[str, List[str]]] = None) -> List[Document]:
        """
        Retrieves routing context using Partitioned MMR (Maximal Marginal Relevance).
        
        Strategy:
        1. Fetch Top-K TABLES using MMR (to ensure diversity of tables).
        2. Fetch Top-K EXAMPLES using MMR (to ensure diversity of intents).
        3. Merge results to avoid "Dominant Intent" masking secondary datasources.
        """
        filter_base = None
        if datasource_id:
            if isinstance(datasource_id, str):
                filter_base = {"datasource_id": datasource_id}
            elif isinstance(datasource_id, list):
                if len(datasource_id) == 1:
                    filter_base = {"datasource_id": datasource_id[0]}
                else:
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
