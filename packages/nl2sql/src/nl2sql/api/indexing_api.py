"""``engine.index``: build, check, clear and inspect the vector index."""

from __future__ import annotations

from typing import Any, Callable, ContextManager, Dict, Iterable, Optional

from nl2sql.api.types import IndexProgress
from nl2sql.runtime import Runtime


# The entry types the index holds, in the order the inspector lists them.
ENTRY_TYPES = ["schema.datasource", "schema.table", "schema.column", "schema.relationship", "schema.metric"]


class IndexAPI:
    """``engine.index``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def build(
        self,
        datasource_ids: Optional[Iterable[str]] = None,
        *,
        full: bool = False,
        curated_only: bool = False,
        enrich: bool = False,
        on_progress: Optional[Callable[[str], None]] = None,
        on_datasource: Optional[Callable[[IndexProgress], None]] = None,
        switch_guard: Optional[Callable[[], ContextManager]] = None,
    ):
        """Build the index for ``datasource_ids`` (every datasource when None), beside the live one.

        Each datasource's new entries are switched in only when all of them
        are written, so a failure keeps its previous entries answering.
        ``full`` rebuilds every datasource into a new collection and swaps it
        in when all succeed (needed after changing the embedding model).
        ``curated_only`` re-indexes the verified queries only. ``enrich`` asks
        the ``indexing_enrichment`` LLM for descriptions and spends tokens.
        ``on_progress`` gets a sentence before each step; ``on_datasource``
        gets an :class:`IndexProgress` as each datasource starts, is done
        (with its entry counts) or fails (with the reason). ``switch_guard``
        is a context manager factory held around each switch.

        Returns:
            A ``RebuildResult``: ``ok``, ``stats``, ``empty`` and ``errors``.

        Raises:
            ValueError: both ``full`` and ``curated_only``.
            EmbeddingModelMismatchError: without ``full``, when the index was
                built with another embedding model.
        """
        from nl2sql.indexing.rebuild import rebuild_index

        return rebuild_index(
            self._ctx,
            enrich=enrich,
            datasource_ids=list(datasource_ids) if datasource_ids is not None else None,
            full=full,
            curated_only=curated_only,
            on_progress=on_progress,
            on_datasource=on_datasource,
            switch_guard=switch_guard,
        )

    def health(self) -> Dict[str, Any]:
        """Health of the vector index.

        ``status`` (``ok``, ``empty``, ``stale`` or ``missing``), entry counts
        by type, when it was built, the embedding model, one entry per
        registered datasource, any problems found, and ``warnings`` that leave
        ``status`` alone (a ``curated.yaml`` changed since it was indexed, a
        preferred table missing from the indexed schema, a datasource that
        failed to start).
        """
        from nl2sql.indexing.curated import curated_hashes
        from nl2sql.indexing.health import IndexHealth, inspect_vector_store

        registry = self._ctx.ds_registry
        unavailable = getattr(registry, "unavailable", None)
        down = [f"Datasource '{ds}' is unavailable: {reason}"
                for ds, reason in (unavailable() if unavailable else {}).items()]
        store = self._ctx.vector_store
        if store is None:
            health = IndexHealth(status="missing", problems=["No vector index is configured."])
        else:
            ids = registry.list_ids() if registry is not None else []
            hashes = curated_hashes(getattr(self._ctx, "curated", None), ids)
            preferred_of = getattr(registry, "get_preferred_tables", None)
            preferred = {ds: preferred_of(ds) for ds in ids} if preferred_of else None
            health = inspect_vector_store(store, self._ctx.schema_store, ids, curated_hashes=hashes,
                                          preferred_tables=preferred)
        health.warnings.extend(down)
        return health.to_dict()

    def clear(self) -> None:
        """Remove every entry from the index. Questions need a ``build`` before they can be answered."""
        self._ctx.vector_store.clear()

    def inspect_defaults(self) -> Dict[str, Any]:
        """What :meth:`inspect` searches with when not told otherwise, as the pipeline searches.

        ``k`` (picks), ``lambda_mult`` (1 is relevance only, 0 is difference
        only), ``fetch_multiplier`` (the candidate pool is ``k`` times it) and
        ``types``, every entry type the index holds.
        """
        from nl2sql.indexing.vector_store import VectorStore

        return {"k": 8, "lambda_mult": VectorStore.LAMBDA_MULT,
                "fetch_multiplier": VectorStore.FETCH_MULTIPLIER, "types": list(ENTRY_TYPES)}

    def inspect(self, query: str, k: int = 8, lambda_mult: Optional[float] = None,
                types=None, datasource_id: Optional[str] = None) -> Dict[str, Any]:
        """One MMR search of the live index, as the engine runs it.

        The candidate pool with scores, the picks in order and what was
        dropped. ``types`` limits the entry types (``schema.table`` ...). It
        reads index metadata only and needs no LLM.

        Raises:
            LookupError: no vector index is configured.
        """
        store = self._ctx.vector_store
        if store is None:
            raise LookupError("There is no vector index configured.")
        kwargs = {"lambda_mult": lambda_mult} if lambda_mult is not None else {}
        return store.inspect(query, k=k, types=types, datasource_id=datasource_id, **kwargs)
