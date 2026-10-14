"""``openai``: OpenAI's embedding models."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings as LangChainEmbeddings

    from nl2sql.config import Embeddings


class OpenAIProvider:
    interface_version = 1
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
        return OpenAIEmbeddings(model=cfg.model or self.default_model, api_key=api_key)


OPENAI = OpenAIProvider()
