from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Callable, ContextManager, Dict, List, Optional

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from nl2sql.indexing.embeddings import (
    PROVIDER_BY_DIMENSION,
    EmbeddingService,
    describe_embeddings,
)
from nl2sql.common.exceptions import NL2SQLError
from nl2sql.common.logger import get_logger
from .models import BaseChunk
from .retrieval_trace import mmr_search

logger = get_logger(__name__)


class EmbeddingDimensionMismatchError(NL2SQLError):
    """Raised when a persisted collection was built with a different embedder."""


class EmbeddingModelMismatchError(NL2SQLError):
    """Raised when the collection records a different embedding model.

    Two models of the same dimension would otherwise mix silently: every
    datasource in the collection must be embedded in the same space as the
    query.
    """


def check_embedding_dimension_compatibility(
    persisted_dimension: Optional[int],
    embeddings: Embeddings,
    collection_name: str,
) -> None:
    """
    Verifies that a persisted collection can be queried with the current embedder.

    Vectors from different embedding providers have different dimensionality
    (OpenAI ``text-embedding-3-small`` is 1536, the local model is 384), so an
    index built with one provider cannot be read with the other.

    Args:
        persisted_dimension: Dimensionality of the persisted vectors, or None
            when the collection is empty or the dimension cannot be determined.
        embeddings: Embedding implementation configured for this process.
        collection_name: Name of the Chroma collection.

    Raises:
        EmbeddingDimensionMismatchError: If the dimensions disagree.
    """
    provider, model, expected_dimension = describe_embeddings(embeddings)

    if persisted_dimension is None or expected_dimension is None:
        return
    if persisted_dimension == expected_dimension:
        return

    built_with = PROVIDER_BY_DIMENSION.get(persisted_dimension)
    origin = (
        f"the '{built_with}' embedding provider"
        if built_with
        else "a different embedding provider"
    )

    raise EmbeddingDimensionMismatchError(
        f"Vector store collection '{collection_name}' was indexed with {origin} "
        f"({persisted_dimension}-dimensional vectors), but the configured provider "
        f"is '{provider}' ({model}, {expected_dimension}-dimensional vectors). "
        "A vector index cannot be shared across embedding providers: re-index with "
        "'nl2sql index' after changing EMBEDDING_PROVIDER, or point VECTOR_STORE at "
        "a separate directory per provider."
    )


class VectorStore:
    """
    Vector store for NL2SQL orchestration.

    This store indexes schema chunks and provides staged retrieval
    for datasource routing, schema grounding, and planning context.
    """

    def __init__(
        self,
        collection_name: str,
        persist_directory: str,
        embeddings: Optional[Embeddings] = None,
    ):
        """
        Initializes the vector store.

        Args:
            collection_name: Name of the Chroma collection.
            persist_directory: Directory used for persistence.
            embeddings: Embedding implementation to use.
        """
        self.collection_name = collection_name
        self.embeddings = embeddings or EmbeddingService.get_embeddings()
        self.persist_directory = persist_directory
        self._initialize_vector_store()

    def _initialize_vector_store(self) -> None:
        """
        Initializes the underlying Chroma vector store.
        """
        self.vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory,
        )
        self._dimension_checked = False

    def _verify_embedding_dimensions(self) -> None:
        """
        Checks the persisted vectors against the configured embedder once per
        store instance.

        The check runs on the read path only: indexing writes a fresh staging
        collection and swaps it in, so a provider switch is fixed by re-running
        ``nl2sql index`` rather than blocked by it.
        """
        if self._dimension_checked:
            return
        check_embedding_dimension_compatibility(
            self._persisted_dimension(),
            self.embeddings,
            self.collection_name,
        )
        self._dimension_checked = True

    def _persisted_dimension(self) -> Optional[int]:
        """
        Reads the dimensionality of the vectors already persisted in the collection.

        Returns:
            Vector dimensionality, or None when the collection is empty or the
            dimension cannot be determined.
        """
        try:
            stored = self.vectorstore._collection.peek(limit=1).get("embeddings")
            if stored is None or len(stored) == 0:
                return None
            return len(stored[0])
        except Exception as exc:
            logger.debug(f"Could not determine persisted embedding dimension: {exc}")
            return None

    def initialize_if_not_exists(self) -> None:
        """
        Initializes the vector store if it does not exist.
        """
        try:
            _ = self.vectorstore._collection.count()
        except Exception:
            logger.info("Vector store not found, initializing new store.")
            self._initialize_vector_store()

        self._verify_embedding_dimensions()

    def is_empty(self) -> bool:
        """
        Checks whether the vector store is empty.

        Returns:
            True if the store contains no documents.
        """
        try:
            return self.vectorstore._collection.count() == 0
        except Exception as exc:
            logger.error(f"Failed to check vector store state: {exc}")
            return True

    # --- one collection, one embedding model, one active build per datasource ---
    #
    # Every datasource lives in this one collection. Its entries carry a
    # ``build_id``; the collection's metadata names each datasource's active
    # build (``build:<datasource_id>``) and when it was switched in
    # (``built_at:<datasource_id>``). Re-indexing a datasource writes a new build
    # beside the active one, switches the pointer only when every entry is
    # written, and then deletes that datasource's other builds. Readers filter
    # on the active builds, so while two builds coexist they see exactly one,
    # and no other datasource's entries are ever read, deleted or rewritten.
    #
    # The collection also records its embedding model (``embedding_model``).
    # A query is embedded in one space, so every datasource must share it; a
    # different configured model is refused until ``nl2sql index --full``
    # rebuilds every datasource into a new collection and swaps it in.

    BUILD_KEY = "build:"
    BUILT_AT_KEY = "built_at:"
    MODEL_KEY = "embedding_model"
    STAGING_SUFFIX = "__staging"
    PREVIOUS_SUFFIX = "__previous"

    def _live_metadata(self) -> Dict[str, Any]:
        """The collection's metadata, re-read so another writer's switch shows."""
        try:
            return dict(self.vectorstore._client.get_collection(self.collection_name).metadata or {})
        except Exception:
            try:
                return dict(self.vectorstore._collection.metadata or {})
            except Exception:
                return {}

    def _update_metadata(self, updates: Dict[str, Any]) -> None:
        # Chroma replaces collection metadata wholesale, so merge first.
        merged = {**self._live_metadata(), **updates}
        self.vectorstore._collection.modify(metadata=merged)

    def embedding_model_id(self) -> str:
        provider, model, _ = describe_embeddings(self.embeddings)
        return f"{provider}/{model}"

    def recorded_embedding_model(self) -> Optional[str]:
        return self._live_metadata().get(self.MODEL_KEY)

    def check_embedding_model(self, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Refuses a configured embedding model other than the recorded one.

        Raises:
            EmbeddingModelMismatchError: If the collection records another model.
        """
        recorded = (metadata if metadata is not None else self._live_metadata()).get(self.MODEL_KEY)
        current = self.embedding_model_id()
        if recorded and recorded != current:
            raise EmbeddingModelMismatchError(
                f"Vector store collection '{self.collection_name}' was built with the embedding model "
                f"'{recorded}', but the configured model is '{current}'. Every datasource in a collection "
                "must use one embedding model, so a model change needs a full rebuild of every "
                "datasource: run 'nl2sql index --full'."
            )

    def active_builds(self, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        md = metadata if metadata is not None else self._live_metadata()
        return {k[len(self.BUILD_KEY):]: v for k, v in md.items() if k.startswith(self.BUILD_KEY)}

    def built_at(self, datasource_id: Optional[str] = None) -> Optional[str]:
        """When a datasource's active build was switched in (the latest of all
        when no datasource is named), ISO 8601 UTC, if recorded."""
        md = self._live_metadata()
        stamps = {k[len(self.BUILT_AT_KEY):]: v for k, v in md.items() if k.startswith(self.BUILT_AT_KEY)}
        if datasource_id is not None:
            return stamps.get(datasource_id)
        return max(stamps.values()) if stamps else None

    def _active_filter(self, datasource_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """A ``where`` clause that keeps only active builds.

        A datasource with no recorded build (an index written before builds
        existed) is read as it is.
        """
        md = self._live_metadata()
        self.check_embedding_model(md)
        builds = self.active_builds(md)
        if datasource_id is not None:
            build = builds.get(datasource_id)
            return {"build_id": build} if build else None
        if not builds:
            return None
        return {
            "$or": [
                {"build_id": {"$in": list(builds.values())}},
                {"datasource_id": {"$nin": list(builds.keys())}},
            ]
        }

    @staticmethod
    def _and(*clauses: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        present = [c for c in clauses if c]
        return present[0] if len(present) == 1 else {"$and": present}

    @staticmethod
    def _public(docs: List[Document]) -> List[Document]:
        """Drops the storage-only build id: it would change every prompt the
        metadata is printed into on every rebuild."""
        for doc in docs:
            doc.metadata.pop("build_id", None)
        return docs

    def entry_metadatas(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """The metadata of every entry (of the active builds), for health reporting."""
        where = self._active_filter() if active_only else None
        got = self.vectorstore._collection.get(where=where, include=["metadatas"]) if where else \
            self.vectorstore._collection.get(include=["metadatas"])
        return list(got["metadatas"] or [])

    def refresh_schema_chunks(
        self,
        datasource_id: str,
        schema_version: str,
        chunks: List[BaseChunk],
        evicted_versions: Optional[List[str]] = None,
        switch_guard: Optional[Callable[[], ContextManager]] = None,
        batch_size: int = 256,
    ) -> Dict[str, int]:
        """
        Rebuilds one datasource's entries: write beside, switch, then delete.

        1. Every chunk is written under a new ``build_id``, in batches. Readers
           ignore it because the datasource's active build is still the old one.
        2. The write is verified, then the active build is switched in the
           collection metadata (under ``switch_guard`` when given, e.g. the
           playground's ``RunGate.change``).
        3. The datasource's other builds are deleted.

        A failure in 1 or 2 deletes the partial new build and re-raises, so the
        previous entries stay active. Other datasources are never touched.

        Args:
            datasource_id: Datasource identifier.
            schema_version: Schema version the chunks were built from.
            chunks: Schema chunks to index.
            evicted_versions: Unused; every previous build of the datasource
                is replaced. Kept for callers of the old signature.
            switch_guard: Context manager factory held around the switch.
            batch_size: Entries embedded and written per call.

        Returns:
            Indexing statistics by chunk type.

        Raises:
            EmbeddingModelMismatchError: If the collection records another model.
        """
        from datetime import datetime, timezone
        from uuid import uuid4

        self.check_embedding_model()
        build_id = uuid4().hex[:12]
        documents = self._prepare_chunk_documents(chunks)
        ids = []
        for i, doc in enumerate(documents):
            doc.metadata["build_id"] = build_id
            ids.append(f"{datasource_id}:{build_id}:{i}")
        this_build = {"$and": [{"datasource_id": datasource_id}, {"build_id": build_id}]}

        try:
            for start in range(0, len(documents), batch_size):
                self.vectorstore.add_documents(
                    documents[start:start + batch_size], ids=ids[start:start + batch_size]
                )
            written = len(self.vectorstore._collection.get(where=this_build, include=[])["ids"])
            if written != len(documents):
                raise RuntimeError(
                    f"Indexing {datasource_id}: wrote {written} of {len(documents)} entries."
                )
            with (switch_guard() if switch_guard else nullcontext()):
                self._update_metadata({
                    self.MODEL_KEY: self.embedding_model_id(),
                    f"{self.BUILD_KEY}{datasource_id}": build_id,
                    f"{self.BUILT_AT_KEY}{datasource_id}": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
        except BaseException:
            try:
                self.vectorstore._collection.delete(where=this_build)
            except Exception as exc:
                logger.error(f"Could not remove the partial build of {datasource_id}: {exc}")
            raise

        # After the switch no reader sees these; a failure here only leaves
        # garbage the next rebuild of this datasource removes.
        try:
            self.vectorstore._collection.delete(
                where={"$and": [{"datasource_id": datasource_id}, {"build_id": {"$ne": build_id}}]}
            )
        except Exception as exc:
            logger.warning(f"Could not delete the previous entries of {datasource_id}: {exc}")

        stats: Dict[str, Any] = {"datasource_id": datasource_id, "schema_version": schema_version}
        for chunk in chunks:
            stats[chunk.type] = stats.get(chunk.type, 0) + 1
        return stats

    # --- full rebuild: a second collection, swapped in ------------------------

    def _collection_names(self) -> List[str]:
        return [c.name for c in self.vectorstore._client.list_collections()]

    def create_staging(self) -> "VectorStore":
        """Returns an empty collection beside this one for a full rebuild.

        A staging collection left by a crashed earlier build is dropped first.
        """
        name = f"{self.collection_name}{self.STAGING_SUFFIX}"
        if name in self._collection_names():
            self.vectorstore._client.delete_collection(name)
        return VectorStore(name, self.persist_directory, embeddings=self.embeddings)

    def discard(self) -> None:
        """Deletes this store's collection; used for an abandoned staging build."""
        try:
            self.vectorstore._client.delete_collection(self.collection_name)
        except Exception as exc:
            logger.warning(f"Could not delete collection '{self.collection_name}': {exc}")

    def promote(self, staging: "VectorStore") -> None:
        """Makes ``staging``'s collection the live one, then re-reads it.

        The live collection is renamed aside first and restored if the second
        rename fails, so the live name always ends up holding a complete index.
        """
        client = self.vectorstore._client
        previous = f"{self.collection_name}{self.PREVIOUS_SUFFIX}"
        names = self._collection_names()
        if previous in names:
            client.delete_collection(previous)
        had_live = self.collection_name in names
        if had_live:
            client.get_collection(self.collection_name).modify(name=previous)
        try:
            staging.vectorstore._collection.modify(name=self.collection_name)
        except Exception:
            if had_live:
                client.get_collection(previous).modify(name=self.collection_name)
            raise
        if had_live:
            client.delete_collection(previous)
        self._initialize_vector_store()

    def clear(self) -> None:
        """
        Deletes the entire vector collection.
        """
        try:
            self.vectorstore.delete_collection()
            self._initialize_vector_store()
        except Exception as exc:
            logger.error(f"Failed to clear vector store: {exc}")

    def delete_documents(self, filter: Dict[str, Any]) -> None:
        """
        Deletes documents matching a metadata filter.

        Args:
            filter: Metadata filter used for deletion.
        """
        try:
            where = (
                {"$and": [{k: v} for k, v in filter.items()]}
                if len(filter) > 1
                else filter
            )
            self.vectorstore._collection.delete(where=where)
        except Exception as exc:
            logger.error(f"Failed to delete documents: {exc}")

    def _prepare_chunk_documents(
        self,
        chunks: List[BaseChunk],
    ) -> List[Document]:
        """
        Converts schema chunks into vector documents.

        Args:
            chunks: Schema chunks to convert.

        Returns:
            List of vector documents.
        """
        return [
            Document(
                page_content=chunk.get_page_content(),
                metadata=chunk.get_metadata(),
            )
            for chunk in chunks
        ]

    # --- retrieval -------------------------------------------------------------
    #
    # Every search is MMR (see ``nl2sql.indexing.retrieval_trace``): fetch the
    # ``k * FETCH_MULTIPLIER`` nearest entries, then pick ``k`` trading relevance
    # (LAMBDA_MULT) against difference from what is already picked. Each
    # ``retrieve_*`` method takes an optional ``explain`` list; when given, the
    # search's record (the query, the pool with scores, the picks in order and
    # what was dropped) is appended to it for the run trace.

    FETCH_MULTIPLIER = 4
    LAMBDA_MULT = 0.7

    def _mmr(
        self,
        search: str,
        query: str,
        where: Optional[Dict[str, Any]],
        k: int,
        explain: Optional[List[Dict[str, Any]]],
        scope: Dict[str, Any],
    ) -> List[Document]:
        from nl2sql.common.resilience import VECTOR_BREAKER

        @VECTOR_BREAKER
        def _execute():
            return mmr_search(
                self.vectorstore._collection,
                self.embeddings,
                query,
                k=k,
                fetch_k=k * self.FETCH_MULTIPLIER,
                lambda_mult=self.LAMBDA_MULT,
                where=where,
            )

        docs, record = _execute()
        if explain is not None:
            explain.append({"search": search, "filter": scope, **record})
        return self._public(docs)

    def retrieve_datasource_candidates(
        self,
        query: str,
        k: int = 3,
        explain: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Document]:
        """
        Retrieves candidate datasources for a user query.

        Args:
            query: User query.
            k: Number of datasource candidates to retrieve.
            explain: When given, the search's record is appended to it.

        Returns:
            Retrieved datasource documents.
        """
        self.initialize_if_not_exists()
        where = self._and({"type": "schema.datasource"}, self._active_filter())
        return self._mmr("datasources", query, where, k, explain, {"types": ["schema.datasource"]})

    def retrieve_schema_context(
        self,
        query: str,
        datasource_id: str,
        k: int = 8,
        explain: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Document]:
        """
        Retrieves schema-level context for a datasource.

        Args:
            query: User query.
            datasource_id: Selected datasource identifier.
            k: Number of schema documents to retrieve.
            explain: When given, the search's record is appended to it.

        Returns:
            Retrieved schema documents.
        """
        self.initialize_if_not_exists()
        types = ["schema.table", "schema.metric"]
        where = self._and(
            {"datasource_id": datasource_id},
            {"type": {"$in": types}},
            self._active_filter(datasource_id),
        )
        return self._mmr("tables", query, where, k, explain, {"datasource_id": datasource_id, "types": types})

    def retrieve_column_candidates(
        self,
        query: str,
        datasource_id: str,
        k: int = 8,
        explain: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Document]:
        """
        Retrieves candidate column documents for a datasource.

        Args:
            query: User query.
            datasource_id: Selected datasource identifier.
            k: Number of column documents to retrieve.
            explain: When given, the search's record is appended to it.

        Returns:
            Retrieved column documents.
        """
        self.initialize_if_not_exists()
        where = self._and(
            {"datasource_id": datasource_id},
            {"type": "schema.column"},
            self._active_filter(datasource_id),
        )
        return self._mmr("columns", query, where, k, explain,
                         {"datasource_id": datasource_id, "types": ["schema.column"]})

    def retrieve_planning_context(
        self,
        query: str,
        datasource_id: str,
        tables: List[str],
        k: int = 12,
        explain: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Document]:
        """
        Retrieves planning-level context for selected tables.

        Args:
            query: User query.
            datasource_id: Selected datasource identifier.
            tables: Fully qualified table names.
            k: Number of planning documents to retrieve.
            explain: When given, the search's record is appended to it.

        Returns:
            Retrieved planning documents.
        """
        self.initialize_if_not_exists()
        types = ["schema.column", "schema.relationship"]
        where = self._and(
            {"datasource_id": datasource_id},
            {"type": {"$in": types}},
            {"table": {"$in": tables}},
            self._active_filter(datasource_id),
        )
        return self._mmr("planning", query, where, k, explain,
                         {"datasource_id": datasource_id, "types": types, "tables": list(tables)})

    def inspect(
        self,
        query: str,
        k: int = 8,
        lambda_mult: float = LAMBDA_MULT,
        types: Optional[List[str]] = None,
        datasource_id: Optional[str] = None,
        fetch_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        """One MMR search over any entry types, for the playground's inspector.

        The same search the engine runs, with the knobs exposed and each
        entry's embedded text included. It reads the active builds only.

        Args:
            query: Text to embed.
            k: How many entries MMR picks.
            lambda_mult: Weight on relevance, 0 to 1.
            types: Entry types to include (``schema.table`` ...); all when empty.
            datasource_id: Only this datasource's entries, when given.
            fetch_k: Pool size; ``k * FETCH_MULTIPLIER`` when not given.

        Returns:
            The search record (see ``retrieval_trace.mmr_search``).
        """
        self.initialize_if_not_exists()
        fetch_k = fetch_k or k * self.FETCH_MULTIPLIER
        clauses = [
            {"type": {"$in": list(types)}} if types else None,
            {"datasource_id": datasource_id} if datasource_id else None,
            self._active_filter(datasource_id),
        ]
        where = self._and(*clauses) if any(clauses) else None
        _, record = mmr_search(
            self.vectorstore._collection, self.embeddings, query,
            k=k, fetch_k=fetch_k, lambda_mult=lambda_mult, where=where, with_text=True,
        )
        return {"filter": {"datasource_id": datasource_id, "types": list(types or [])}, **record}
