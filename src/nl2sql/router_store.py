from __future__ import annotations

from typing import List, Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from nl2sql.settings import settings
from nl2sql.datasource_config import DatasourceProfile

class DatasourceRouterStore:
    """
    Manages vector indexing and retrieval of datasource descriptions.
    
    Used to route user queries to the most relevant database based on domain descriptions.
    """

    def __init__(self, collection_name: str = "datasource_router", embeddings: Optional[Embeddings] = None, persist_directory: str = "./chroma_db"):
        self.collection_name = collection_name
        self.embeddings = embeddings or OpenAIEmbeddings(api_key=settings.openai_api_key)
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

    def index_datasources(self, datasources: List[DatasourceProfile], examples_path: Optional[str] = None):
        """
        Indexes datasource descriptions and optionally example questions.
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
        
        # Index Examples (Layer 1)
        if examples_path:
            import yaml
            import pathlib
            
            path = pathlib.Path(examples_path)
            if path.exists():
                try:
                    examples = yaml.safe_load(path.read_text()) or {}
                    for ds_id, questions in examples.items():
                        for q in questions:
                            doc = Document(
                                page_content=q,
                                metadata={"datasource_id": ds_id, "type": "example"}
                            )
                            documents.append(doc)
                    print(f"Indexed {sum(len(q) for q in examples.values())} example questions.")
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
        from langchain_core.prompts import PromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        prompt = PromptTemplate(
            template="""You are an AI language model assistant. Your task is to generate 3 different versions of the given user question to retrieve relevant documents from a vector database. By generating multiple perspectives on the user question, your goal is to help the user overcome some of the limitations of the distance-based similarity search. Provide these alternative questions separated by newlines. Original question: {question}""",
            input_variables=["question"]
        )

        chain = prompt | llm | StrOutputParser()
        
        try:
            variations = chain.invoke({"question": query}).split("\n")
            variations = [v.strip() for v in variations if v.strip()]
            # Add original query
            variations.append(query)
            
            print(f"  -> Generated {len(variations)-1} variations: {variations[:-1]}")
            
            votes = {}
            for q in variations:
                # Use retrieve_with_score to check confidence
                results = self.retrieve_with_score(q, k=1)
                if results:
                    ds_id, distance = results[0]
                    # Only count vote if distance is reasonable (e.g. < 0.5)
                    # If it's garbage, don't vote.
                    if distance < 0.5:
                        votes[ds_id] = votes.get(ds_id, 0) + 1
            
            if not votes:
                print("  -> No variations met confidence threshold.")
                return []
                
            # Return winner
            winner = max(votes, key=votes.get)
            print(f"  -> Voting results: {votes}. Winner: {winner}")
            return [winner]
            
        except Exception as e:
            print(f"  -> Multi-query generation failed: {e}")
            return []

    def llm_route(self, query: str, llm, datasources: List[DatasourceProfile]) -> Optional[str]:
        """
        Layer 3: Uses an LLM to reason about which datasource is best.
        """
        from langchain_core.prompts import PromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        # Format datasource descriptions
        iterable = datasources.values() if isinstance(datasources, dict) else datasources
        ds_context = "\n".join([f"- ID: {ds.id}\n  Description: {ds.description}" for ds in iterable])

        prompt = PromptTemplate(
            template="""You are a database routing expert. Your goal is to select the most relevant database for a user's SQL query.

Available Databases:
{context}

User Query: "{question}"

Instructions:
1. Analyze the query and match it to the database descriptions.
2. Return ONLY the ID of the selected database.
3. If no database is relevant, return "None".

Selected Database ID:""",
            input_variables=["context", "question"]
        )

        chain = prompt | llm | StrOutputParser()
        
        try:
            result = chain.invoke({"context": ds_context, "question": query}).strip()
            # Clean up potential extra text
            result = result.split()[0].strip().strip('"').strip("'")
            
            iterable = datasources.values() if isinstance(datasources, dict) else datasources
            valid_ids = {ds.id for ds in iterable}
            if result in valid_ids:
                return result
            return None
        except Exception as e:
            print(f"  -> LLM routing failed: {e}")
            return None
