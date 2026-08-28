from __future__ import annotations

import pytest
from langchain_core.documents import Document

from nl2sql.indexing.embeddings import LOCAL_EMBEDDING_DIMENSION, LocalEmbeddings
from nl2sql.indexing.vector_store import VectorStore

pytestmark = pytest.mark.integration


def test_local_embeddings_round_trip_through_chroma(tmp_path):
    """Downloads the ONNX model on first run; needs no API key."""
    embeddings = LocalEmbeddings()

    assert len(embeddings.embed_query("hello")) == LOCAL_EMBEDDING_DIMENSION

    store = VectorStore(
        collection_name="nl2sql_store",
        persist_directory=str(tmp_path),
        embeddings=embeddings,
    )
    store.vectorstore.add_documents(
        [
            Document(page_content="orders table holds customer purchases"),
            Document(page_content="weather station rainfall readings"),
        ]
    )

    hits = store.vectorstore.similarity_search("where are purchases stored", k=1)

    assert hits[0].page_content == "orders table holds customer purchases"
