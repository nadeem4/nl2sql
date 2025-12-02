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
