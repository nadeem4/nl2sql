"""``openai`` and ``openai_compatible``: embeddings over OpenAI's embeddings API.

``openai`` is OpenAI's own service. ``openai_compatible`` is any other server
with an OpenAI-style embeddings API: Azure OpenAI's v1 endpoint, Ollama, vLLM,
LiteLLM or a company gateway. There is no Azure-specific provider.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict

from nl2sql.embeddings import EmbeddingInfo

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings as LangChainEmbeddings

    from nl2sql.config import Embeddings

# The output dimension of OpenAI's own embedding models.
OPENAI_EMBEDDING_DIMENSIONS: Dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

# Sent when an openai_compatible server needs no key: the OpenAI client refuses to start without one.
PLACEHOLDER_KEY = "not-needed"


class OpenAIProvider:
    interface_version = 2
    default_model = "text-embedding-3-small"
    requires = ("langchain_openai",)
    install_hint = 'pip install "nl2sql-engine"'

    def create(self, cfg: "Embeddings") -> "LangChainEmbeddings":
        """Raises ValueError naming the key variable when there is no key."""
        from langchain_openai import OpenAIEmbeddings

        from nl2sql.llm.providers import OPENAI_ENV, api_key_from_env

        api_key = cfg.api_key or api_key_from_env("openai")
        if not api_key:
            raise ValueError(f"The openai embeddings provider needs an API key. Set {OPENAI_ENV} or give "
                             "'embeddings' an 'api_key'.")
        return OpenAIEmbeddings(model=cfg.model or self.default_model, api_key=api_key, base_url=cfg.base_url)

    def describe(self, cfg: "Embeddings") -> EmbeddingInfo:
        model = cfg.model or self.default_model
        return EmbeddingInfo("openai", model, cfg.dimension or OPENAI_EMBEDDING_DIMENSIONS.get(model))


class OpenAICompatibleProvider:
    interface_version = 2
    default_model = None  # model is required: there is no default across servers
    requires = ("langchain_openai",)
    install_hint = 'pip install "nl2sql-engine"'

    def create(self, cfg: "Embeddings") -> "LangChainEmbeddings":
        """Raises ValueError when ``model`` or ``base_url`` is missing."""
        missing = [name for name in ("model", "base_url") if not getattr(cfg, name)]
        if missing:
            raise ValueError(f"The openai_compatible embeddings provider needs {' and '.join(missing)}: "
                             "the server's URL (for example http://ollama:11434/v1) and the model it serves.")
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=cfg.model,
            base_url=cfg.base_url,
            # Never OPENAI_API_KEY: that key belongs to OpenAI, not to this server.
            api_key=cfg.api_key or PLACEHOLDER_KEY,
            # Send text: OpenAI's token ids mean nothing to another server's model.
            check_embedding_ctx_length=False,
        )

    def describe(self, cfg: "Embeddings") -> EmbeddingInfo:
        return EmbeddingInfo("openai_compatible", cfg.model or "", cfg.dimension)


OPENAI = OpenAIProvider()
OPENAI_COMPATIBLE = OpenAICompatibleProvider()
