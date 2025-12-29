from __future__ import annotations

from typing import Optional
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings
from nl2sql.core.settings import settings

class EmbeddingService:
    """
    Centralized service for managing embedding models.
    Ensures consistency across the application.
    """
    
    _instance: Optional[Embeddings] = None

    @classmethod
    def get_embeddings(cls) -> Embeddings:
        """
        Returns the configured embeddings instance.
        Lazy loads the instance.
        """
        if cls._instance is None:
            cls._instance = OpenAIEmbeddings(
                model=settings.embedding_model,
                api_key=settings.openai_api_key
            )
        return cls._instance

    @classmethod
    def get_model_name(cls) -> str:
        """Returns the name of the configured embedding model."""
        return settings.embedding_model
