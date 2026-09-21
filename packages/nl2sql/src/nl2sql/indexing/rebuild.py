"""Index rebuilds that never leave an empty or half-built index behind.

Two shapes, one entry point (:func:`rebuild_index`), used by ``nl2sql index``,
the demo's startup repair and the playground's Rebuild button:

**Per datasource (the default).** All datasources share one collection. Each
datasource is rebuilt on its own with
:meth:`VectorStore.refresh_schema_chunks`: new entries under a new build id,
a switch of that datasource's active build once all are written, then the
old entries deleted. A failure keeps that datasource's previous entries
active, and no other datasource's entries are read, deleted or rewritten.

**Full (``full=True``).** For a change of embedding model, the one case where
every datasource must move at once because a query is embedded in a single
space. Every datasource is built into a staging collection that is swapped in
(:meth:`VectorStore.promote`) only when all of them succeed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, ContextManager, Dict, Iterable, List, Optional

from nl2sql.common.logger import get_logger
from nl2sql.indexing.orchestrator import IndexingOrchestrator

logger = get_logger("indexing_rebuild")

MODEL_STEP = "Loading the embedding model (the first run downloads about 79 MB)"


@dataclass
class RebuildResult:
    """What a rebuild did.

    ``ok`` is True only when every requested datasource's new entries are live.
    """

    stats: List[Dict[str, Any]] = field(default_factory=list)
    empty: List[str] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    ok: bool = False


def rebuild_index(
    ctx,
    enrich: bool = True,
    datasource_ids: Optional[Iterable[str]] = None,
    full: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
    on_datasource: Optional[Callable[[str, str, Optional[str]], None]] = None,
    switch_guard: Optional[Callable[[], ContextManager]] = None,
) -> RebuildResult:
    """Rebuilds the vector index.

    Args:
        ctx: An ``NL2SQLContext`` (or anything with ``vector_store`` and
            ``ds_registry``).
        enrich: Ask the LLM for descriptions; spends tokens.
        datasource_ids: Only these datasources; all registered ones when None.
            Ignored with ``full``, which always rebuilds every datasource.
        full: Rebuild every datasource into a new collection and swap it in;
            required after changing the embedding model.
        on_progress: Called with a sentence before each step.
        on_datasource: Called with ``(datasource_id, "start"|"done"|"failed",
            error)`` so the CLI can print a line per datasource.
        switch_guard: A context manager factory held only around each switch,
            e.g. the playground's ``RunGate.change`` so questions in flight
            finish first.

    Returns:
        A :class:`RebuildResult`.

    Raises:
        EmbeddingModelMismatchError: Without ``full``, when the collection
            records a different embedding model than the configured one.
    """
    say = on_progress or (lambda _msg: None)
    tell = on_datasource or (lambda *_args: None)
    result = RebuildResult()
    live = ctx.vector_store

    adapters = list(ctx.ds_registry.list_adapters())
    if datasource_ids is not None and not full:
        wanted = list(dict.fromkeys(datasource_ids))
        known = {a.datasource_id for a in adapters}
        for ds_id in wanted:
            if ds_id not in known:
                result.errors.append({"datasource_id": ds_id, "error": "No such datasource is configured."})
        adapters = [a for a in adapters if a.datasource_id in wanted]
        if result.errors:
            return result

    if not full:
        # Before any work: a per-datasource rebuild must embed in the model
        # the collection already holds.
        live.check_embedding_model()

    target = live.create_staging() if full else live
    try:
        say(MODEL_STEP)
        # Embeddings load lazily; warming them here puts the one slow,
        # first-run-only download under its own step.
        target.embeddings.embed_query("warm up")

        orchestrator = IndexingOrchestrator(ctx, enrich=enrich)
        for adapter in adapters:
            ds_id = adapter.datasource_id
            say(f"Indexing {ds_id}: reading the schema and embedding its entries")
            tell(ds_id, "start", None)
            try:
                stats = orchestrator.index_datasource(
                    adapter,
                    vector_store=target,
                    switch_guard=None if full else switch_guard,
                )
            except Exception as exc:
                logger.error(f"Failed to index {ds_id}: {exc}")
                result.errors.append({"datasource_id": ds_id, "error": str(exc)})
                tell(ds_id, "failed", str(exc))
                continue
            if stats:
                result.stats.append(stats)
            else:
                result.empty.append(ds_id)
            tell(ds_id, "done", None)

        if not full:
            result.ok = not result.errors and bool(result.stats)
            return result

        if result.errors or not result.stats:
            target.discard()
            return result
        say("Switching every datasource to the new index")
        from contextlib import nullcontext

        with (switch_guard() if switch_guard else nullcontext()):
            live.promote(target)
        result.ok = True
        return result
    except BaseException:
        if full:
            target.discard()
        raise
