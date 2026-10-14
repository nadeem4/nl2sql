"""A real ``chroma`` index store in a temporary folder, written the way indexing writes it.

For tests of retrieval, health and the playground: ``TestIndex(tmp_path,
embeddings).write("chinook", chunks)`` commits one build of ``chunks`` for the
datasource (``IndexingOrchestrator._commit``), and ``retriever()`` reads the
live builds, as a run does. ``memory_state()`` is the built-in state store on
``:memory:``.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from nl2sql.embeddings import EmbeddingInfo
from nl2sql.indexing.orchestrator import IndexingOrchestrator
from nl2sql.indexing.retrieval import Retriever, namespace_for
from nl2sql.storage.chroma import ChromaIndex
from nl2sql.storage.sqlite import SqliteState


def memory_state() -> SqliteState:
    """The built-in state store on ``:memory:``, migrated."""
    state = SqliteState(":memory:")
    state.migrate()
    return state


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

    def __init__(self, folder, embeddings, state: Any = None, model: str = "test"):
        self.index = ChromaIndex(folder / "index")
        self.embeddings = embeddings
        self.state = state if state is not None else memory_state()
        state = self.state
        dimension = len(embeddings.embed_query("dimension"))
        self.namespace = namespace_for(EmbeddingInfo("test", model, dimension))
        self.ctx = SimpleNamespace(
            index=self.index, namespace=self.namespace, embedding_info=EmbeddingInfo("test", model, dimension),
            embedder=embeddings, state=state, ds_registry=None, llm_registry=None, curated=None,
            settings=SimpleNamespace(schema_=SimpleNamespace(max_versions=3)))

    def write(self, datasource_id: str, chunks: List[Any], schema_version: str = "v1",
              curated_hash: Optional[str] = None) -> str:
        """Commits one build of ``chunks`` for ``datasource_id``; returns its build id."""
        return IndexingOrchestrator(self.ctx)._commit(datasource_id, schema_version, list(chunks), [], curated_hash)

    def retriever(self) -> Retriever:
        return Retriever(self.index, self.namespace, self.embeddings, self.index.pointers(self.namespace),
                         state=self.state)

    def close(self) -> None:
        self.index.close()
