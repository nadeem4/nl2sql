"""``engine.index``: build, check, clear, prune and inspect the vector index."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

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
        curated_only: bool = False,
        enrich: bool = False,
        on_progress: Optional[Callable[[str], None]] = None,
        on_datasource: Optional[Callable[[IndexProgress], None]] = None,
    ):
        """Build the index for ``datasource_ids`` (every datasource when None), beside the live one.

        Each datasource's new entries are written into the namespace of the
        configured embedding model, and its build pointer is switched to them
        only when all are written, so a failure keeps its previous build and
        schema version answering. Runs in flight keep the build they started
        with. ``curated_only`` re-indexes the verified queries only.
        ``enrich`` asks the ``indexing_enrichment`` LLM for descriptions and
        spends tokens. ``on_progress`` gets a sentence before each step;
        ``on_datasource`` gets an :class:`IndexProgress` as each datasource
        starts, is done (with its entry counts) or fails (with the reason).

        Returns:
            A ``RebuildResult``: ``ok``, ``stats``, ``empty`` and ``errors``.
        """
        from nl2sql.indexing.rebuild import rebuild_index

        return rebuild_index(
            self._ctx,
            enrich=enrich,
            datasource_ids=list(datasource_ids) if datasource_ids is not None else None,
            curated_only=curated_only,
            on_progress=on_progress,
            on_datasource=on_datasource,
        )

    def health(self) -> Dict[str, Any]:
        """Health of the vector index of the configured embedding model.

        ``status`` (``ok``, ``empty``, ``stale`` or ``missing``), entry counts
        by type, when it was built, the embedding model, the namespace, one
        entry per registered datasource, any problems found, and ``warnings``
        that leave ``status`` alone (a ``curated.yaml`` changed since it was
        indexed, a preferred table missing from the indexed schema, a
        datasource that failed to start).
        """
        from nl2sql.indexing.curated import curated_hashes
        from nl2sql.indexing.health import index_health

        ctx = self._ctx
        registry = ctx.ds_registry
        down = [f"Datasource '{ds}' is unavailable: {reason}" for ds, reason in registry.unavailable().items()]
        ids = registry.list_ids()
        info = ctx.embedding_info
        health = index_health(
            ctx.index, ctx.namespace, ctx.state, ids,
            embedding_model=f"{info.provider}/{info.model}",
            curated_hashes=curated_hashes(getattr(ctx, "curated", None), ids),
            preferred_tables={ds: registry.get_preferred_tables(ds) for ds in ids},
        )
        health.warnings.extend(down)
        return health.to_dict()

    def clear(self) -> None:
        """Remove every entry of the configured embedding model's index. Questions need a ``build`` first."""
        self._ctx.index.drop(self._ctx.namespace)

    def prune(self) -> List[str]:
        """Drop the index namespaces of every other embedding model, and return their names.

        This includes 0.5's ``nl2sql_store`` collection, which this version
        never reads. The current model's namespace is kept.
        """
        index, current = self._ctx.index, self._ctx.namespace
        dropped = [name for name in index.namespaces() if name != current]
        for name in dropped:
            index.drop(name)
        return dropped

    def inspect_defaults(self) -> Dict[str, Any]:
        """What :meth:`inspect` searches with when not told otherwise, as the pipeline searches.

        ``k`` (picks), ``lambda_mult`` (1 is relevance only, 0 is difference
        only), ``fetch_multiplier`` (the candidate pool is ``k`` times it) and
        ``types``, every entry type the index holds.
        """
        from nl2sql.indexing.retrieval import Retriever

        return {"k": 8, "lambda_mult": Retriever.LAMBDA_MULT,
                "fetch_multiplier": Retriever.FETCH_MULTIPLIER, "types": list(ENTRY_TYPES)}

    def inspect(self, query: str, k: int = 8, lambda_mult: Optional[float] = None,
                types=None, datasource_id: Optional[str] = None) -> Dict[str, Any]:
        """One MMR search of the live index, as the engine runs it.

        The candidate pool with scores, the picks in order and what was
        dropped. ``types`` limits the entry types (``schema.table`` ...). It
        reads index metadata only and needs no LLM.
        """
        kwargs = {"lambda_mult": lambda_mult} if lambda_mult is not None else {}
        return self._ctx.vector_store.inspect(query, k=k, types=types, datasource_id=datasource_id, **kwargs)
