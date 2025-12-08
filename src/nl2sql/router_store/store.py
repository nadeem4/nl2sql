from __future__ import annotations

from typing import List, Optional, Dict

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from nl2sql.settings import settings
from nl2sql.datasource_config import DatasourceProfile
from nl2sql.embeddings import EmbeddingService

from .agents import canonicalize_query, enrich_question, generate_query_variations, decided_best_datasource

class DatasourceRouterStore:
    """
    Manages vector indexing and retrieval of datasource descriptions.
    
    Used to route user queries to the most relevant database based on domain descriptions.
    """

    def __init__(self, collection_name: str = "datasource_router", embeddings: Optional[Embeddings] = None, persist_directory: str = "./chroma_db"):
        self.collection_name = collection_name
        self.embeddings = embeddings or EmbeddingService.get_embeddings()
        self.persist_directory = persist_directory
        self.vectorstore = Chroma(
            collection_name=collection_name,
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )

    def clear(self):
        """Clears the router collection."""
        try:
            self.vectorstore.delete_collection()
            self.vectorstore = Chroma(
                collection_name=self.collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_directory
            )
        except Exception:
            pass

    
    def canonicalize_query(self, query: str, llm) -> str:
        return canonicalize_query(query, llm)

    def index_datasources(self, datasources: List[DatasourceProfile], schemas: Optional[Dict[str, List[str]]] = None, examples_path: Optional[str] = None, llm=None):
        """
        Indexes datasource descriptions, schema summaries, and optional example questions.
        Applies canonicalization and enrichment if an LLM is provided.
        """
        documents = []
        
        # Index Descriptions
        for ds in datasources.values() if isinstance(datasources, dict) else datasources:
            if ds.description:
                content = f"Datasource: {ds.id}. Description: {ds.description}"
                doc = Document(
                    page_content=content,
                    metadata={"datasource_id": ds.id, "type": "description"}
                )
                documents.append(doc)
                
        # Index Schema Summaries (Synthetic Examples)
        if schemas:
            for ds_id, tables in schemas.items():
                for table in tables:
                     # Generate synthetic questions likely to match this table
                     # e.g. "machines" -> "machines", "table machines", "show machines"
                     synonyms = [
                         f"{table}",
                         f"table {table}",
                         f"show {table}",
                         f"list {table}",
                         f"{table} data"
                     ]
                     for syn in synonyms:
                         doc = Document(
                             page_content=syn,
                             metadata={"datasource_id": ds_id, "type": "schema_summary"}
                         )
                         documents.append(doc)
            print(f"Indexed schema summaries for {len(schemas)} datasources.")
        
        # Index Examples (Layer 1)
        if examples_path:
            import yaml
            import pathlib
            
            path = pathlib.Path(examples_path)
            if path.exists():
                try:
                    examples = yaml.safe_load(path.read_text()) or {}
                    total_variants = 0
                    for ds_id, questions in examples.items():
                        print(f"Processing examples for {ds_id}...")
                        for q in questions:
                            # 1. Canonicalize (if LLM available)
                            canonical_q = q
                            if llm:
                                # We canonicalize the *stored* question too, so it matches the *canonicalized* input query
                                canonical_q = canonicalize_query(q, llm)
                            
                            # 2. Enrich (Generate variants of the canonical form)
                            variants = [canonical_q] # Always include the canonical form
                            if llm:
                                generated_variants = enrich_question(canonical_q, llm)
                                variants.extend(generated_variants)
                            else:
                                variants.append(q) # Fallback to just raw if no LLM

                            # Deduplicate
                            variants = list(set(variants))
                            total_variants += len(variants)

                            for v in variants:
                                doc = Document(
                                    page_content=v,
                                    metadata={"datasource_id": ds_id, "type": "example", "original": q}
                                )
                                documents.append(doc)
                    print(f"Indexed {sum(len(q) for q in examples.values())} original examples => {total_variants} enriched variations.")
                except Exception as e:
                    print(f"Failed to load routing examples: {e}")

        if documents:
            self.vectorstore.add_documents(documents)

    def retrieve(self, query: str, k: int = 1) -> List[str]:
        """
        Retrieves the most relevant datasource ID for a query.
        """
        docs = self.vectorstore.similarity_search(query, k=k)
        return [doc.metadata["datasource_id"] for doc in docs]

    def retrieve_with_score(self, query: str, k: int = 1) -> List[tuple[str, float]]:
        """
        Retrieves datasource IDs with their similarity scores.
        Note: Chroma returns distance (lower is better).
        """
        docs_and_scores = self.vectorstore.similarity_search_with_score(query, k=k)
        return [(doc.metadata["datasource_id"], score) for doc, score in docs_and_scores]

    def multi_query_retrieve(self, query: str, llm, k: int = 1) -> List[str]:
        """
        Generates query variations and retrieves the most voted datasource.
        Only counts votes if the match distance is below a threshold (0.5).
        """
        variations = generate_query_variations(query, llm)
        
        # Add original query
        variations.append(query)
        
        print(f"  -> Generated {len(variations)-1} variations (last one is original).")
        
        votes = {}
        for q in variations:
            # Use retrieve_with_score to check confidence
            results = self.retrieve_with_score(q, k=1)
            if results:
                ds_id, distance = results[0]
                # Only count vote if distance is reasonable
                # If it's garbage, don't vote.
                if distance < settings.router_l2_threshold:
                    votes[ds_id] = votes.get(ds_id, 0) + 1
        
        if not votes:
            print("  -> No variations met confidence threshold.")
            return []
            
        # Return winner
        winner = max(votes, key=votes.get)
        print(f"  -> Voting results: {votes}. Winner: {winner}")
        return [winner]

    def llm_route(self, query: str, llm, datasources: List[DatasourceProfile]) -> tuple[Optional[str], str]:
        """
        Layer 3: Uses an LLM to reason about which datasource is best.
        Returns: (datasource_id, reasoning)
        """
        return decided_best_datasource(query, llm, datasources)
