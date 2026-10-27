"""Builds one datasource's index entries and commits them by switching its build pointer.

A build, in order (docs/architecture/phase2.md, section 4):

1. The schema snapshot is stored in the state store, as an immutable row. It
   holds only what the database said (B31): a model's draft never enters it,
   and a person's words stay in the glossary. It gets a ``schema_version`` (an
   unchanged snapshot gets its old one), and nothing reads it yet.
2. Every entry is embedded by the engine's embedder and written to the index
   store's namespace under a new ``build_id``. Readers do not see them: they
   read only the builds the pointers name.
3. The datasource's ``BuildPointer`` is switched to the new build and its
   schema version, recording the hashes of the verified queries and the
   knowledge it was made with. That one write is the commit.
4. The datasource's builds other than the new one and the one just replaced
   are deleted, so a run that pinned the previous pointer keeps its entries.
5. When the new version's structure differs from the version it replaced, the
   diff between them is stored beside the new version and ``schema.drifted``
   is published (D31, docs/architecture/wave_a.md, section 10). A version
   whose statistics alone changed gets no diff.
6. Its snapshots are deleted except the versions a pointer names (in any
   namespace), the one just replaced, and the newest
   ``settings.schema.max_versions``.

A failure before step 3 leaves the previous pointer, and so the previous
build and schema version, live. Its stored snapshot and partial entries are
referenced by no pointer; the next build of the datasource deletes the entries.

``refresh`` makes a build from the live build's stored snapshot, the current
verified queries and the current knowledge: no schema read, no model call, and
only the entries whose text changed are embedded.

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
from nl2sql.indexing.models import BaseChunk
from nl2sql.knowledge.merge import Knowledge
from nl2sql.schema import SchemaSnapshot
from nl2sql.schema.diff import diff_contracts
from nl2sql.schema.drift import publish_drift
from nl2sql_sdk.knowledge import DatasourceKnowledge
from nl2sql_sdk.schema import IndexIssue, SchemaContract
from nl2sql_sdk.stores import BuildPointer, Entry, Filter

if TYPE_CHECKING:
    from nl2sql.config.curated import CuratedDatasource
    from nl2sql.runtime import Runtime

logger = get_logger("indexing_orchestrator")

BATCH_SIZE = 256

# ``index.column_failed``'s message for the stages that read the data itself.
_FIXED_MESSAGES = {"values": "The column's values could not be read", "profile": "The column could not be profiled"}


def entry_metadata(chunk: BaseChunk) -> Dict[str, Any]:
    """A chunk's metadata as an index entry holds it: scalars, with no empty values."""
    return {key: value for key, value in chunk.get_metadata().items() if value is not None}


class IndexingOrchestrator:
    """Indexes datasources into the engine's index store, one build per datasource."""

    def __init__(self, ctx: "Runtime"):
        """
        Args:
            ctx: The engine's runtime.
        """
        self.index = ctx.index
        self.namespace = ctx.namespace
        self.dimension = ctx.embedding_info.dimension
        self.embeddings = ctx.embedder
        self.state = ctx.state
        self.max_versions = ctx.settings.schema_.max_versions
        self.ds_registry = ctx.ds_registry
        # A build runs outside any question, so its events go on the engine's bus.
        self.bus = ctx.bus
        self.tenant_id = ctx.tenant_id
        self._ctx = ctx  # for the redactor's secrets
        # The loaded curated.yaml, or None when the feature is off.
        self.curated = getattr(ctx, "curated", None)

    def index_datasource(self, adapter: DatasourceAdapter) -> Dict[str, Any]:
        """Reads the datasource's schema, builds its entries and switches its pointer to them.

        Returns:
            Entry counts by type, with ``datasource_id`` and ``schema_version``.
        """
        datasource_id = adapter.datasource_id
        schema_snapshot = adapter.fetch_schema_snapshot(self.ds_registry.get_index_scope(datasource_id))

        datasource_description = self.ds_registry.get_description(datasource_id)

        # The description lives in the datasources config, not the database,
        # so no adapter can report it. It is the operator's own words about
        # what the datasource holds and is what the resolver matches questions
        # against, so it wins over anything the adapter wrote.
        if datasource_description and datasource_description.strip():
            metadata = schema_snapshot.metadata.model_copy(
                update={"description": datasource_description.strip()}
            )
            schema_snapshot = schema_snapshot.model_copy(update={"metadata": metadata})

        # Stored first, read by nothing until the pointer names it.
        schema_version = self.state.put_snapshot(schema_snapshot)

        # Verified queries and knowledge go in the same build: a build replaces
        # every entry of its datasource, so entries written on their own would
        # be deleted by the next schema re-index.
        curated = curated_section(self.curated, datasource_id)
        chunks = self._chunks(datasource_id, schema_snapshot, schema_version, curated)

        for issue in schema_snapshot.issues:
            # The issue only: never the column's statistics or sample values (D15).
            self._publish("index.column_failed", {"datasource_id": datasource_id, "table": issue.table,
                                                  "column": issue.column, "stage": issue.stage,
                                                  "message": self._issue_message(issue)})
        previous = self.index.pointers(self.namespace).get(datasource_id)
        self._commit(datasource_id, schema_version, chunks, [], curated_hash(curated), self._digest(datasource_id))
        if previous is not None:
            self._record_drift(datasource_id, previous.schema_version, schema_version, schema_snapshot.contract)
        self._keep_snapshots(datasource_id, previous.schema_version if previous else None)
        stats = _stats(datasource_id, schema_version, [c.type for c in chunks])
        if schema_snapshot.issues:
            # What a table or column was indexed without: reported, not only logged.
            stats["issues"] = [issue.model_dump(exclude_none=True) for issue in schema_snapshot.issues]
            logger.warning(f"Indexed {datasource_id} with {len(schema_snapshot.issues)} issue(s); "
                           f"the first: {schema_snapshot.issues[0].table}: {schema_snapshot.issues[0].message}")
        return stats

    def _knowledge(self, datasource_id: str) -> DatasourceKnowledge:
        """The datasource's merged knowledge as the engine holds it now; none without a ``knowledge`` section."""
        knowledge = getattr(self._ctx, "knowledge", None)
        return knowledge.get(datasource_id) if knowledge is not None else DatasourceKnowledge()

    def _digest(self, datasource_id: str) -> str:
        """The digest of that knowledge, which the pointer records and health compares."""
        knowledge = getattr(self._ctx, "knowledge", None) or Knowledge()
        return knowledge.digest(datasource_id)

    def _dialect(self, datasource_id: str) -> Optional[str]:
        try:
            return self.ds_registry.get_dialect(datasource_id)
        except Exception:
            return None

    def _chunks(self, datasource_id: str, snapshot: SchemaSnapshot, schema_version: str,
                curated: Optional["CuratedDatasource"]) -> List[BaseChunk]:
        """Every entry of the datasource's build: the schema and the knowledge, then the verified queries."""
        return SchemaChunkBuilder(
            ds_id=datasource_id, schema_snapshot=snapshot, schema_version=schema_version,
            questions=self.ds_registry.get_sample_questions(datasource_id),
            knowledge=self._knowledge(datasource_id), dialect=self._dialect(datasource_id),
        ).build() + CuratedChunkBuilder(datasource_id, curated, schema_version).build()

    def _issue_message(self, issue: IndexIssue) -> str:
        """What ``index.column_failed`` says of ``issue``, never a value from the data (D15).

        Reading a column's values or statistics can fail on a value (a cast
        error quotes it), so those two stages say only what failed and the
        error's type. The other stages' driver words are redacted.
        """
        fixed = _FIXED_MESSAGES.get(issue.stage)
        if fixed is not None:
            return f"{fixed} ({issue.error_type})." if issue.error_type else f"{fixed}."
        from nl2sql.tracing.trace import redact_for

        return redact_for(self._ctx, issue.message)

    def _publish(self, name: str, data: Dict[str, Any]) -> None:
        self.bus.publish(name, data, tenant_id=self.tenant_id)

    def _record_drift(self, datasource_id: str, replaced: str, schema_version: str,
                      contract: SchemaContract) -> None:
        """Stores the diff from the replaced version to the new one and publishes ``schema.drifted``.

        Only for a structural change. A failure is logged and never fails the
        build, which is already live.
        """
        if replaced == schema_version:
            return
        try:
            old = self.state.get_snapshot(datasource_id, replaced)
            if old is None:
                return
            diff = diff_contracts(old.contract, contract, from_version=replaced, to_version=schema_version)
            if diff.is_empty():
                return
            self.state.put_schema_diff(diff)
            publish_drift(self._ctx, diff, "build")
        except Exception as exc:
            logger.warning(f"Could not record the schema diff of {datasource_id} ({replaced} to "
                           f"{schema_version}): {exc}")

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

    def refresh(self, datasource_id: str) -> Dict[str, Any]:
        """Writes the datasource's entries again from what is stored and what is configured now.

        Reads no schema and calls no model: every entry's text is rendered
        from the live build's stored snapshot, the current verified queries and
        the current knowledge. An entry whose text and metadata equal the live
        build's keeps its stored vector; only the changed and new entries are
        embedded. The pointer keeps its schema version and records the current
        hashes.

        Raises:
            LookupError: If the datasource has never been indexed, or its
                stored snapshot is gone.
        """
        live = self.index.pointers(self.namespace).get(datasource_id)
        if live is None:
            raise LookupError(
                f"Datasource '{datasource_id}' has no index entries to keep; "
                f"run `nl2sql index --datasource {datasource_id}` first."
            )
        snapshot = self.state.get_snapshot(datasource_id, live.schema_version)
        if snapshot is None:
            raise LookupError(
                f"The schema snapshot {live.schema_version} of '{datasource_id}' is no longer stored; "
                f"run `nl2sql index --datasource {datasource_id}`."
            )
        curated = curated_section(self.curated, datasource_id)
        chunks = self._chunks(datasource_id, snapshot, live.schema_version, curated)
        stored = {e.metadata.get("id"): e for e in
                  self.index.get(self.namespace, Filter(eq={"build_id": live.build_id}), with_vectors=True)}
        kept: List[Entry] = []
        changed: List[BaseChunk] = []
        for chunk in chunks:
            old = stored.get(chunk.id)
            if (old is not None and old.text == chunk.get_page_content()
                    and {k: v for k, v in old.metadata.items() if k != "build_id"} == entry_metadata(chunk)):
                kept.append(old)
            else:
                changed.append(chunk)
        self._commit(datasource_id, live.schema_version, changed, kept, curated_hash(curated),
                     self._digest(datasource_id))
        return _stats(datasource_id, live.schema_version,
                      [str(e.metadata.get("type", "unknown")) for e in kept] + [c.type for c in changed])

    def _commit(self, datasource_id: str, schema_version: str, chunks: List[BaseChunk],
                copied: List[Entry], hash_: Optional[str], knowledge_hash: Optional[str] = None) -> str:
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
            knowledge_hash=knowledge_hash,
        )})
        logger.info(f"Index pointer for {datasource_id} switched to build {build_id} "
                    f"(schema version {schema_version}) in {self.namespace}")
        counts: Dict[str, int] = {}
        for kind in [str(e.metadata.get("type", "unknown")) for e in copied] + [c.type for c in chunks]:
            counts[kind] = counts.get(kind, 0) + 1
        self._publish("index.swapped", {
            "datasource_id": datasource_id, "namespace": self.namespace, "build_id": build_id,
            "schema_version": schema_version, "counts": counts,
            "replaced_build_id": previous.build_id if previous else None,
            "replaced_schema_version": previous.schema_version if previous else None})

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
