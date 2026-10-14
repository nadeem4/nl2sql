from __future__ import annotations

import pytest

from nl2sql.embeddings.local import LOCAL_EMBEDDING_DIMENSION, LocalEmbeddings
from nl2sql.storage.chroma import ChromaIndex
from nl2sql_sdk.stores import Entry

pytestmark = pytest.mark.integration

NAMESPACE = "nl2sql__local-all-minilm-l6-v2__384"


def test_local_embeddings_round_trip_through_the_chroma_index_store(tmp_path):
    """Downloads the ONNX model on first run; needs no API key."""
    embeddings = LocalEmbeddings()

    assert len(embeddings.embed_query("hello")) == LOCAL_EMBEDDING_DIMENSION

    index = ChromaIndex(tmp_path / "index")
    index.open(NAMESPACE, LOCAL_EMBEDDING_DIMENSION)
    texts = ["orders table holds customer purchases", "weather station rainfall readings"]
    index.add(NAMESPACE, [
        Entry(id=str(i), text=text, vector=vector)
        for i, (text, vector) in enumerate(zip(texts, embeddings.embed_documents(texts)))
    ])

    hits = index.query(NAMESPACE, embeddings.embed_query("where are purchases stored"), k=1)

    assert hits[0].text == "orders table holds customer purchases"
