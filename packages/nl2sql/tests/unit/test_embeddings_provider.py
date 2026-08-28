from __future__ import annotations

import sys
import types

import numpy as np
import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from nl2sql.common.settings import settings
from nl2sql.indexing.embeddings import (
    LOCAL_EMBEDDING_DIMENSION,
    EmbeddingService,
    LocalEmbeddings,
)
from nl2sql.indexing.vector_store import (
    EmbeddingDimensionMismatchError,
    VectorStore,
    check_embedding_dimension_compatibility,
)


@pytest.fixture(autouse=True)
def reset_embedding_cache(monkeypatch):
    """Keep the process-wide embedder cache from leaking between tests."""
    monkeypatch.setattr(EmbeddingService, "_instance", None)
    monkeypatch.setattr(EmbeddingService, "_instance_provider", None)


@pytest.fixture
def stub_default_embedding_function(monkeypatch):
    """Replace chromadb's ONNX embedder so unit tests never download the model."""
    calls: list[list[str]] = []

    class _StubEmbeddingFunction:
        def __call__(self, texts):
            calls.append(list(texts))
            return np.array([[1.0, 2.0, 3.0]] * len(texts), dtype=np.float32)

    module = types.ModuleType("chromadb.utils.embedding_functions")
    module.DefaultEmbeddingFunction = _StubEmbeddingFunction
    monkeypatch.setitem(sys.modules, "chromadb.utils.embedding_functions", module)
    return calls


def test_get_embeddings_defaults_to_openai(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")

    assert isinstance(EmbeddingService.get_embeddings(), OpenAIEmbeddings)


def test_get_embeddings_returns_local_provider(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "local")

    assert isinstance(EmbeddingService.get_embeddings(), LocalEmbeddings)


def test_get_embeddings_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "bogus")

    with pytest.raises(ValueError) as excinfo:
        EmbeddingService.get_embeddings()

    message = str(excinfo.value)
    assert "bogus" in message
    assert "openai" in message
    assert "local" in message


def test_get_embeddings_cache_follows_provider_change(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    assert isinstance(EmbeddingService.get_embeddings(), OpenAIEmbeddings)

    monkeypatch.setattr(settings, "embedding_provider", "local")
    assert isinstance(EmbeddingService.get_embeddings(), LocalEmbeddings)


def test_get_model_name_follows_provider(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "embedding_model", "text-embedding-3-small")
    assert EmbeddingService.get_model_name() == "text-embedding-3-small"

    monkeypatch.setattr(settings, "embedding_provider", "local")
    assert EmbeddingService.get_model_name() == "all-MiniLM-L6-v2"


def test_local_embeddings_returns_plain_float_lists(stub_default_embedding_function):
    vectors = LocalEmbeddings().embed_documents(["a", "b"])

    assert vectors == [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]]
    assert all(type(value) is float for vector in vectors for value in vector)
    assert stub_default_embedding_function == [["a", "b"]]


def test_local_embeddings_embed_query_returns_flat_vector(stub_default_embedding_function):
    vector = LocalEmbeddings().embed_query("a")

    assert vector == [1.0, 2.0, 3.0]
    assert all(type(value) is float for value in vector)


def test_dimension_guard_rejects_index_from_other_provider():
    with pytest.raises(EmbeddingDimensionMismatchError) as excinfo:
        check_embedding_dimension_compatibility(
            persisted_dimension=1536,
            embeddings=LocalEmbeddings(),
            collection_name="nl2sql_store",
        )

    message = str(excinfo.value)
    assert "nl2sql_store" in message
    assert "1536" in message
    assert str(LOCAL_EMBEDDING_DIMENSION) in message
    assert "openai" in message
    assert "local" in message
    assert "re-index" in message.lower()


def test_dimension_guard_allows_matching_index():
    check_embedding_dimension_compatibility(
        persisted_dimension=LOCAL_EMBEDDING_DIMENSION,
        embeddings=LocalEmbeddings(),
        collection_name="nl2sql_store",
    )


def test_dimension_guard_skips_when_dimension_unknown():
    check_embedding_dimension_compatibility(
        persisted_dimension=None,
        embeddings=LocalEmbeddings(),
        collection_name="nl2sql_store",
    )
    check_embedding_dimension_compatibility(
        persisted_dimension=1536,
        embeddings=OpenAIEmbeddings(model="some-future-model", api_key="sk-test"),
        collection_name="nl2sql_store",
    )


class _FakeEmbeddings(Embeddings):
    """Deterministic stand-in with a declared model name and dimensionality."""

    def __init__(self, model: str, dimension: int) -> None:
        self.model = model
        self.dimension = dimension

    def embed_documents(self, texts):
        return [[0.01] * self.dimension for _ in texts]

    def embed_query(self, text):
        return [0.01] * self.dimension


def _openai_like() -> _FakeEmbeddings:
    return _FakeEmbeddings("text-embedding-3-small", 1536)


def _local_like() -> _FakeEmbeddings:
    return _FakeEmbeddings("all-MiniLM-L6-v2", LOCAL_EMBEDDING_DIMENSION)


def test_retrieval_rejects_index_built_by_another_provider(tmp_path):
    store = VectorStore("nl2sql_store", str(tmp_path), embeddings=_openai_like())
    store.vectorstore.add_documents(
        [Document(page_content="orders", metadata={"type": "schema.datasource"})]
    )

    reopened = VectorStore("nl2sql_store", str(tmp_path), embeddings=_local_like())

    with pytest.raises(EmbeddingDimensionMismatchError):
        reopened.retrieve_datasource_candidates("anything")


def test_reindexing_recovers_from_a_provider_switch(tmp_path):
    store = VectorStore("nl2sql_store", str(tmp_path), embeddings=_openai_like())
    store.vectorstore.add_documents(
        [Document(page_content="orders", metadata={"type": "schema.datasource"})]
    )

    # Re-indexing must not be blocked by the guard: it clears the collection first.
    reindexed = VectorStore("nl2sql_store", str(tmp_path), embeddings=_local_like())
    reindexed.clear()
    reindexed.vectorstore.add_documents(
        [Document(page_content="orders table", metadata={"type": "schema.datasource"})]
    )

    hits = reindexed.retrieve_datasource_candidates("orders")

    assert hits[0].page_content == "orders table"
