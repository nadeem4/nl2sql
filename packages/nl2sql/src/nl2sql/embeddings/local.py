"""``local``: the key-free ONNX all-MiniLM-L6-v2 embedder bundled with chromadb."""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from langchain_core.embeddings import Embeddings as LangChainEmbeddings

from nl2sql.common.logger import get_logger
from nl2sql.embeddings import EmbeddingInfo

if TYPE_CHECKING:
    from nl2sql.config import Embeddings

logger = get_logger(__name__)

LOCAL_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
LOCAL_EMBEDDING_DIMENSION = 384


class LocalEmbeddings(LangChainEmbeddings):
    """Key-free embeddings: the ONNX all-MiniLM-L6-v2 model bundled with chromadb, 384 dimensions.

    Only chromadb's embedding function is used, not its index; the model is
    built on the first call.
    """

    def __init__(self) -> None:
        self._embedding_function = None

    def _get_embedding_function(self):
        if self._embedding_function is None:
            # Imported here so nothing pays for chromadb's ONNX runtime unless
            # local embeddings are actually used.
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

            logger.info(
                f"Initializing local embeddings ({LOCAL_EMBEDDING_MODEL}). "
                "The first run downloads the ONNX model (~79 MB) into the local "
                "cache directory, which can take a few minutes."
            )
            self._embedding_function = DefaultEmbeddingFunction()
        return self._embedding_function

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """One plain float vector per text."""
        vectors = self._get_embedding_function()(list(texts))
        return [[float(value) for value in vector] for vector in vectors]

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


class LocalProvider:
    interface_version = 2
    default_model = LOCAL_EMBEDDING_MODEL
    requires = ("chromadb", "onnxruntime")
    install_hint = 'pip install "nl2sql-engine"'

    def create(self, cfg: "Embeddings") -> "LangChainEmbeddings":
        """Raises ValueError when the config names another model, a ``base_url`` or another dimension."""
        if cfg.model and cfg.model != self.default_model:
            raise ValueError(f"The local embeddings provider has one model, {self.default_model}; "
                             f"the config names '{cfg.model}'. Remove 'model' or use another provider.")
        if cfg.base_url is not None:
            raise ValueError("The local embeddings provider runs in-process and takes no base_url. "
                             "Remove 'base_url', or use 'openai_compatible' for an embeddings server.")
        if cfg.dimension is not None and cfg.dimension != LOCAL_EMBEDDING_DIMENSION:
            raise ValueError(f"The local embeddings model returns {LOCAL_EMBEDDING_DIMENSION} dimensions; "
                             f"the config says {cfg.dimension}. Remove 'dimension'.")
        return LocalEmbeddings()

    def describe(self, cfg: "Embeddings") -> EmbeddingInfo:
        return EmbeddingInfo("local", self.default_model, LOCAL_EMBEDDING_DIMENSION)


LOCAL = LocalProvider()
