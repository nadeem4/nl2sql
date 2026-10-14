"""A real ``chroma`` index store in a temporary folder, written the way indexing writes it.

For tests of retrieval, health and the playground: ``TestIndex(tmp_path,
embeddings).write("chinook", chunks)`` commits one build of ``chunks`` for the
datasource (``IndexingOrchestrator._commit``), and ``retriever()`` reads the
live builds, as a run does.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from nl2sql.embeddings import EmbeddingInfo
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.indexing.retrieval import Retriever, namespace_for
from nl2sql.storage.chroma import ChromaIndex


class Chunk:
    """The two methods indexing needs from a chunk, with whatever metadata a test gives."""

    def __init__(self, text: str, **metadata: Any):
        self.type = metadata.get("type", "schema.table")
        self._text = text
        self._md = metadata

    def get_page_content(self) -> str:
        return self._text

    def get_metadata(self) -> Dict[str, Any]:
        return dict(self._md)


class TestIndex:
    __test__ = False  # not a test class

    def __init__(self, folder, embeddings, schema_store: Any = None, model: str = "test"):
        self.index = ChromaIndex(folder / "index")
        self.embeddings = embeddings
        self.schema_store = schema_store
        dimension = len(embeddings.embed_query("dimension"))
        self.namespace = namespace_for(EmbeddingInfo("test", model, dimension))
        self.ctx = SimpleNamespace(
            index=self.index, namespace=self.namespace, embedding_info=EmbeddingInfo("test", model, dimension),
            embedder=embeddings, schema_store=schema_store, ds_registry=None, llm_registry=None, curated=None)

    def write(self, datasource_id: str, chunks: List[Any], schema_version: str = "v1",
              curated_hash: Optional[str] = None) -> str:
        """Commits one build of ``chunks`` for ``datasource_id``; returns its build id."""
        return IndexingOrchestrator(self.ctx)._commit(datasource_id, schema_version, list(chunks), [], curated_hash)

    def retriever(self) -> Retriever:
        return Retriever(self.index, self.namespace, self.embeddings, self.index.pointers(self.namespace),
                         schema_store=self.schema_store)

    def close(self) -> None:
        self.index.close()
