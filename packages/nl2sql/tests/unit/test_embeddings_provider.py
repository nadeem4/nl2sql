"""The local embedder: chromadb's bundled ONNX model, returning plain float vectors.

An index built with another embedding model is not refused any more: each model
has its own index namespace (``test_index_pointer_commit.py``).
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from nl2sql.embeddings.local import LocalEmbeddings


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


def test_local_embeddings_returns_plain_float_lists(stub_default_embedding_function):
    vectors = LocalEmbeddings().embed_documents(["a", "b"])

    assert vectors == [[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]]
    assert all(type(value) is float for vector in vectors for value in vector)
    assert stub_default_embedding_function == [["a", "b"]]


def test_local_embeddings_embed_query_returns_flat_vector(stub_default_embedding_function):
    vector = LocalEmbeddings().embed_query("a")

    assert vector == [1.0, 2.0, 3.0]
    assert all(type(value) is float for value in vector)
