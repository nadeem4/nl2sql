"""Builds one datasource's index entries and commits them by switching its build pointer.

A build, in order (docs/architecture/phase2.md, section 4):

1. The schema snapshot is stored in the state store, as an immutable row. It
   gets a ``schema_version`` (an unchanged snapshot gets its old one), and
   nothing reads it yet.
2. Every entry is embedded by the engine's embedder and written to the index
   store's namespace under a new ``build_id``. Readers do not see them: they
   read only the builds the pointers name.
3. The datasource's ``BuildPointer`` is switched to the new build and its
   schema version. That one write is the commit.
4. The datasource's builds other than the new one and the one just replaced
   are deleted, so a run that pinned the previous pointer keeps its entries.
5. Its snapshots are deleted except the versions a pointer names (in any
   namespace), the one just replaced, and the newest
   ``settings.schema.max_versions``.

A failure before step 3 leaves the previous pointer, and so the previous
build and schema version, live. Its stored snapshot and partial entries are
referenced by no pointer; the next build of the datasource deletes the entries.

``engine.index.build`` runs every build holding the state store's ``index``
lock (``indexing/rebuild.py``), so two processes sharing a ``storage.path``
never build at once.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

from nl2sql.common.logger import get_logger
from nl2sql.datasources.protocols import DatasourceAdapter
from nl2sql.indexing.chunk_builder import SchemaChunkBuilder
from nl2sql.indexing.curated import CuratedChunkBuilder, curated_hash, curated_section
from nl2sql.indexing.enrichment_service import enrich_schema_snapshot
from nl2sql.indexing.models import BaseChunk
from nl2sql.indexing.retrieval import CURATED_TYPE
from nl2sql_sdk.stores import BuildPointer, Entry, Filter

if TYPE_CHECKING:
    from nl2sql.runtime import Runtime

logger = get_logger("indexing_orchestrator")

BATCH_SIZE = 256


def entry_metadata(chunk: BaseChunk) -> Dict[str, Any]:
    """A chunk's metadata as an index entry holds it: scalars, with no empty values."""
    return {key: value for key, value in chunk.get_metadata().items() if value is not None}


class IndexingOrchestrator:
    """Indexes datasources into the engine's index store, one build per datasource."""

    def __init__(self, ctx: "Runtime", enrich: bool = False):
        """
        Args:
            ctx: The engine's runtime.
            enrich: Whether to ask the ``indexing_enrichment`` LLM for
                descriptions. Off by default: enrichment spends tokens, so
                every caller turns it on only when asked.
        """
        self.enrich = enrich
        self.index = ctx.index
        self.namespace = ctx.namespace
        self.dimension = ctx.embedding_info.dimension
        self.embeddings = ctx.embedder
        self.state = ctx.state
        self.max_versions = ctx.settings.schema_.max_versions
        self.ds_registry = ctx.ds_registry
        self.llm_registry = ctx.llm_registry
        # The loaded curated.yaml, or None when the feature is off.
        self.curated = getattr(ctx, "curated", None)

    def index_datasource(self, adapter: DatasourceAdapter) -> Dict[str, Any]:
        """Reads the datasource's schema, builds its entries and switches its pointer to them.

        Returns:
            Entry counts by type, with ``datasource_id`` and ``schema_version``.
        """
        datasource_id = adapter.datasource_id
        schema_snapshot = adapter.fetch_schema_snapshot(self.ds_registry.get_index_scope(datasource_id))

        questions = self.ds_registry.get_sample_questions(datasource_id)
        datasource_description = self.ds_registry.get_description(datasource_id)

        # Best-effort: enrich_schema_snapshot owns resolving its own LLM and
        # degrades to the unenriched snapshot when none is usable, so indexing
        # never depends on an API key being present.
        if self.enrich:
            schema_snapshot, questions = enrich_schema_snapshot(
                snapshot=schema_snapshot,
                llm_registry=self.llm_registry,
                datasource_description=datasource_description,
                existing_questions=questions,
            )

        # The description lives in the datasources config, not the database,
        # so no adapter can report it. It is the operator's own words about
        # what the datasource holds and is what the resolver matches questions
        # against, so it wins over anything the adapter or enrichment wrote.
        if datasource_description and datasource_description.strip():
            metadata = schema_snapshot.metadata.model_copy(
                update={"description": datasource_description.strip()}
            )
            schema_snapshot = schema_snapshot.model_copy(update={"metadata": metadata})

        # Stored first, read by nothing until the pointer names it.
        schema_version = self.state.put_snapshot(schema_snapshot)

        # Verified queries go in the same build: a build replaces every entry
        # of its datasource, so curated entries written on their own would be
        # deleted by the next schema re-index.
        curated = curated_section(self.curated, datasource_id)
        chunks = SchemaChunkBuilder(
            ds_id=datasource_id, schema_snapshot=schema_snapshot,
            schema_version=schema_version, questions=questions,
        ).build() + CuratedChunkBuilder(datasource_id, curated, schema_version).build()

        previous = self.index.pointers(self.namespace).get(datasource_id)
        self._commit(datasource_id, schema_version, chunks, [], curated_hash(curated))
        self._keep_snapshots(datasource_id, previous.schema_version if previous else None)
        stats = _stats(datasource_id, schema_version, [c.type for c in chunks])
        if schema_snapshot.issues:
            # What a table or column was indexed without: reported, not only logged.
            stats["issues"] = [issue.model_dump(exclude_none=True) for issue in schema_snapshot.issues]
            logger.warning(f"Indexed {datasource_id} with {len(schema_snapshot.issues)} issue(s); "
                           f"the first: {schema_snapshot.issues[0].table}: {schema_snapshot.issues[0].message}")
        return stats

    def _keep_snapshots(self, datasource_id: str, replaced: Optional[str]) -> None:
        """Deletes the datasource's snapshots that nothing can read any more.

        Kept: every version a pointer names, in every namespace (another
        embedding model's build may still be switched back to); the version
        just replaced, which runs in flight may have pinned; and the newest
        ``settings.schema.max_versions``. A failure only leaves old snapshots.
        """
        try:
            keep = {pointer.schema_version for namespace in self.index.namespaces()
                    for ds_id, pointer in self.index.pointers(namespace).items() if ds_id == datasource_id}
            keep.update(self.state.list_versions(datasource_id)[:self.max_versions])
            if replaced:
                keep.add(replaced)
            self.state.keep_snapshots(datasource_id, keep)
        except Exception as exc:
            logger.warning(f"Could not delete the old schema snapshots of {datasource_id}: {exc}")

    def refresh_curated(self, datasource_id: str) -> Dict[str, Any]:
        """Re-indexes a datasource's verified queries only.

        Reads no schema and calls no model: the live build's schema entries
        are copied into the new build with their stored vectors, so only the
        verified queries are embedded, and the pointer keeps its schema version.

        Raises:
            LookupError: If the datasource has never been indexed.
        """
        live = self.index.pointers(self.namespace).get(datasource_id)
        if live is None:
            raise LookupError(
                f"Datasource '{datasource_id}' has no index entries to keep; "
                f"run `nl2sql index --datasource {datasource_id}` first."
            )
        kept = self.index.get(self.namespace, Filter(eq={"build_id": live.build_id}, ne={"type": CURATED_TYPE}),
                              with_vectors=True)
        curated = curated_section(self.curated, datasource_id)
        chunks = CuratedChunkBuilder(datasource_id, curated, live.schema_version).build()
        self._commit(datasource_id, live.schema_version, chunks, kept, curated_hash(curated))
        return _stats(datasource_id, live.schema_version,
                      [str(e.metadata.get("type", "unknown")) for e in kept] + [c.type for c in chunks])

    def _commit(self, datasource_id: str, schema_version: str, chunks: List[BaseChunk],
                copied: List[Entry], hash_: Optional[str]) -> str:
        """Writes a new build of ``chunks`` (embedded) and ``copied`` (as they are), then switches to it.

        Returns the new build id.
        """
        build_id = uuid4().hex[:12]
        self.index.open(self.namespace, self.dimension)

        entries = [Entry(id=f"{datasource_id}:{build_id}:{i}", text=e.text, vector=e.vector,
                         metadata={**e.metadata, "build_id": build_id})
                   for i, e in enumerate(copied)]
        self.index.add(self.namespace, entries)
        for start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[start:start + BATCH_SIZE]
            texts = [c.get_page_content() for c in batch]
            vectors = self.embeddings.embed_documents(texts)
            self.index.add(self.namespace, [
                Entry(id=f"{datasource_id}:{build_id}:{len(copied) + start + i}", text=text,
                      vector=[float(v) for v in vector], metadata={**entry_metadata(chunk), "build_id": build_id})
                for i, (chunk, text, vector) in enumerate(zip(batch, texts, vectors))
            ])

        written = len(self.index.get(self.namespace, Filter(eq={"build_id": build_id})))
        expected = len(copied) + len(chunks)
        if written != expected:
            raise RuntimeError(f"Indexing {datasource_id}: wrote {written} of {expected} entries.")

        previous = self.index.pointers(self.namespace).get(datasource_id)
        self.index.set_pointers(self.namespace, {datasource_id: BuildPointer(
            build_id=build_id, schema_version=schema_version,
            built_at=datetime.now(timezone.utc).replace(microsecond=0), curated_hash=hash_,
        )})
        logger.info(f"Index pointer for {datasource_id} switched to build {build_id} "
                    f"(schema version {schema_version}) in {self.namespace}")

        # After the switch no new run reads these. The replaced build is kept
        # for runs that pinned it; older builds and failed ones go. A failure
        # here only leaves entries the next build of this datasource removes.
        keep = {build_id} | ({previous.build_id} if previous else set())
        try:
            builds = {e.metadata.get("build_id")
                      for e in self.index.get(self.namespace, Filter(eq={"datasource_id": datasource_id}))}
            stale = sorted(b for b in builds - keep if b)
            if stale:
                self.index.delete(self.namespace, Filter(eq={"datasource_id": datasource_id},
                                                         **{"in": {"build_id": stale}}))
        except Exception as exc:
            logger.warning(f"Could not delete the old entries of {datasource_id}: {exc}")
        return build_id


def _stats(datasource_id: str, schema_version: str, types: List[str]) -> Dict[str, Any]:
    stats: Dict[str, Any] = {"datasource_id": datasource_id, "schema_version": schema_version}
    for kind in types:
        stats[kind] = stats.get(kind, 0) + 1
    return stats
