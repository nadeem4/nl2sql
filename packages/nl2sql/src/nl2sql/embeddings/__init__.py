"""The embeddings provider interface: the code behind ``embeddings.provider``.

A provider is registered under the ``nl2sql.embeddings`` entry point group, or
in code with ``Registry.add_embeddings``. The built-ins are ``local`` and
``openai``. See docs/architecture/configuration_v2.md, "Interfaces and versions".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Protocol, Tuple, runtime_checkable

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings as LangChainEmbeddings

    from nl2sql.config import Embeddings


@runtime_checkable
class EmbeddingsProvider(Protocol):
    """An embeddings provider.

    The registry checks ``interface_version`` and finds each of ``requires``
    before the provider is used, reporting ``install_hint`` when one is
    missing. ``create`` is given the ``embeddings`` section with its key
    already resolved; ``model`` None means ``default_model``.
    """

    interface_version: ClassVar[int]
    default_model: str
    requires: Tuple[str, ...]
    install_hint: str

    def create(self, cfg: "Embeddings") -> "LangChainEmbeddings":
        """The embedder for ``cfg``. Builds it without calling the model."""
        ...


__all__ = ["EmbeddingsProvider"]
