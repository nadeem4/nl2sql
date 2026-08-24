from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from nl2sql.common.logger import get_logger
from nl2sql.common.settings import settings

logger = get_logger(__name__)

SUPPORTED_EMBEDDING_PROVIDERS: Tuple[str, ...] = ("openai", "local")

LOCAL_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
LOCAL_EMBEDDING_DIMENSION = 384

# Output dimensions of the embedding models this project knows about. Used only
# to detect an index built with a different embedder; unknown models are skipped.
KNOWN_EMBEDDING_DIMENSIONS: Dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    LOCAL_EMBEDDING_MODEL: LOCAL_EMBEDDING_DIMENSION,
}

PROVIDER_BY_DIMENSION: Dict[int, str] = {
    LOCAL_EMBEDDING_DIMENSION: "local",
    1536: "openai",
    3072: "openai",
}


class LocalEmbeddings(Embeddings):
    """
    Key-free embeddings backed by the ONNX all-MiniLM-L6-v2 model bundled with
    chromadb. Produces 384-dimensional vectors and requires no API key.
    """

    def __init__(self) -> None:
        self._embedding_function = None

    def _get_embedding_function(self):
        """
        Lazily builds chromadb's default embedding function.

        Returns:
            The chromadb embedding function instance.
        """
        if self._embedding_function is None:
            # Imported here so nothing pays for chromadb's ONNX runtime unless
            # local embeddings are actually selected.
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            logger.info(
                f"Initializing local embeddings ({LOCAL_EMBEDDING_MODEL}). "
                "The first run downloads the ONNX model (~79 MB) into the local "
                "cache directory, which can take a few minutes."
            )
            self._embedding_function = DefaultEmbeddingFunction()
        return self._embedding_function

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Embeds a batch of documents.

        Args:
            texts: Documents to embed.

        Returns:
            One plain float vector per document.
        """
        vectors = self._get_embedding_function()(list(texts))
        return [[float(value) for value in vector] for vector in vectors]

    def embed_query(self, text: str) -> List[float]:
        """
        Embeds a single query.

        Args:
            text: Query to embed.

        Returns:
            A flat plain float vector.
        """
        return self.embed_documents([text])[0]


def describe_embeddings(embeddings: Embeddings) -> Tuple[str, str, Optional[int]]:
    """
    Describes an embedder for diagnostics.

    Args:
        embeddings: Embedding implementation in use.

    Returns:
        Tuple of provider name, model name and known output dimension. The
        dimension is None when the model is not a known one.
    """
    if isinstance(embeddings, LocalEmbeddings):
        return "local", LOCAL_EMBEDDING_MODEL, LOCAL_EMBEDDING_DIMENSION

    model = getattr(embeddings, "model", None)
    provider = "openai" if isinstance(embeddings, OpenAIEmbeddings) else type(embeddings).__name__
    dimension = KNOWN_EMBEDDING_DIMENSIONS.get(model) if model else None
    return provider, model or "unknown", dimension


class EmbeddingService:
    """
    Centralized service for managing embedding models.
    Ensures consistency across the application.
    """

    _instance: Optional[Embeddings] = None
    _instance_provider: Optional[str] = None

    @classmethod
    def get_embeddings(cls) -> Embeddings:
        """
        Returns the embeddings instance for the configured provider.

        The instance is cached per provider so a runtime settings reload
        (``reload_settings``) cannot hand back an embedder for the old provider.
        """
        provider = cls._resolve_provider()
        if cls._instance is None or cls._instance_provider != provider:
            cls._instance = cls._build_embeddings(provider)
            cls._instance_provider = provider
        return cls._instance

    @classmethod
    def _resolve_provider(cls) -> str:
        """Returns the normalized embedding provider from settings."""
        return (settings.embedding_provider or "openai").strip().lower()

    @classmethod
    def _build_embeddings(cls, provider: str) -> Embeddings:
        """
        Builds an embeddings instance for a provider.

        Args:
            provider: Normalized provider name.

        Returns:
            The embeddings implementation.

        Raises:
            ValueError: If the provider is not recognized.
        """
        if provider == "openai":
            return OpenAIEmbeddings(
                model=settings.embedding_model,
                api_key=settings.openai_api_key,
            )
        if provider == "local":
            return LocalEmbeddings()

        raise ValueError(
            f"Unknown EMBEDDING_PROVIDER '{provider}'. "
            f"Valid options are: {', '.join(SUPPORTED_EMBEDDING_PROVIDERS)}."
        )

    @classmethod
    def get_model_name(cls) -> str:
        """Returns the name of the configured embedding model."""
        if cls._resolve_provider() == "local":
            return LOCAL_EMBEDDING_MODEL
        return settings.embedding_model
