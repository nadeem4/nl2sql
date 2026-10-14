"""The embeddings provider interface: the code behind ``embeddings.provider``.

A provider is registered under the ``nl2sql.embeddings`` entry point group, or
in code with ``Registry.add_embeddings``. The built-ins are ``local``,
``openai`` and ``openai_compatible``. See docs/architecture/phase2.md,
section 5, "Embeddings".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, NamedTuple, Optional, Protocol, Tuple, runtime_checkable

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings as LangChainEmbeddings

    from nl2sql.config import Embeddings

# The text embedded once when an embedder's dimension is not known.
DIMENSION_PROBE = "dimension probe"


class EmbeddingInfo(NamedTuple):
    """What an embedder is: the engine names the index namespace from it.

    ``dimension`` None means "not known without a call": the engine probes it.
    """

    provider: str
    model: str
    dimension: Optional[int]


@runtime_checkable
class EmbeddingsProvider(Protocol):
    """An embeddings provider, interface version 2.

    The registry checks ``interface_version`` and finds each of ``requires``
    before the provider is used, reporting ``install_hint`` when one is
    missing. ``create`` and ``describe`` are given the ``embeddings`` section
    with its key already resolved; ``model`` None means ``default_model``.
    """

    interface_version: ClassVar[int]
    default_model: Optional[str]
    requires: Tuple[str, ...]
    install_hint: str

    def create(self, cfg: "Embeddings") -> "LangChainEmbeddings":
        """The embedder for ``cfg``. Builds it without calling the model.

        Raises ValueError when ``cfg`` is not one this provider can build.
        """
        ...

    def describe(self, cfg: "Embeddings") -> EmbeddingInfo:
        """The provider, model and dimension ``create(cfg)`` embeds with. Never calls the model;
        the dimension is None when only a call would tell."""
        ...


def probe(info: EmbeddingInfo, embedder: "LangChainEmbeddings") -> EmbeddingInfo:
    """``info`` with its dimension: as described, or from embedding ``DIMENSION_PROBE`` once."""
    if info.dimension is not None:
        return info
    return info._replace(dimension=len(embedder.embed_query(DIMENSION_PROBE)))


__all__ = ["DIMENSION_PROBE", "EmbeddingInfo", "EmbeddingsProvider", "probe"]
