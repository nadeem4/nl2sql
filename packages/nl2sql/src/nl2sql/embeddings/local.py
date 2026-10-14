"""``local``: the key-free ONNX all-MiniLM-L6-v2 embedder bundled with chromadb."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings as LangChainEmbeddings

    from nl2sql.config import Embeddings

LOCAL_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class LocalProvider:
    interface_version = 1
    default_model = LOCAL_EMBEDDING_MODEL
    requires = ("chromadb", "onnxruntime")
    install_hint = 'pip install "nl2sql-engine"'

    def create(self, cfg: "Embeddings") -> "LangChainEmbeddings":
        """Raises ValueError when the config names a model other than the bundled one."""
        if cfg.model and cfg.model != self.default_model:
            raise ValueError(f"The local embeddings provider has one model, {self.default_model}; "
                             f"the config names '{cfg.model}'. Remove 'model' or use another provider.")
        from nl2sql.indexing.embeddings import LocalEmbeddings

        return LocalEmbeddings()


LOCAL = LocalProvider()
