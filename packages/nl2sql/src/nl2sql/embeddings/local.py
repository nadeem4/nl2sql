"""``local``: the key-free ONNX all-MiniLM-L6-v2 embedder bundled with chromadb."""
from __future__ import annotations

from typing import TYPE_CHECKING

from nl2sql.embeddings import EmbeddingInfo

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings as LangChainEmbeddings

    from nl2sql.config import Embeddings

LOCAL_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
LOCAL_EMBEDDING_DIMENSION = 384


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
        from nl2sql.indexing.embeddings import LocalEmbeddings

        return LocalEmbeddings()

    def describe(self, cfg: "Embeddings") -> EmbeddingInfo:
        return EmbeddingInfo("local", self.default_model, LOCAL_EMBEDDING_DIMENSION)


LOCAL = LocalProvider()
