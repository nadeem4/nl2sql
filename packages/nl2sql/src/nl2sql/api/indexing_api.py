"""``engine.index``: build, clear, prune and inspect the vector index, and measure schema drift."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional

from nl2sql.api.types import IndexProgress
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime

if TYPE_CHECKING:
    from nl2sql.policies import Scope
    from nl2sql.schema.drift import DriftReport, SchemaVersionInfo
    from nl2sql_sdk.schema import SchemaDiff


# The entry types the index holds, in the order the inspector lists them.
ENTRY_TYPES = ["schema.datasource", "schema.datasource_example", "schema.table", "schema.column",
               "schema.relationship", "schema.metric", "knowledge.term"]


class IndexAPI:
    """``engine.index``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def build(
        self,
        datasource_ids: Optional[Iterable[str]] = None,
        *,
        on_progress: Optional[Callable[[str], None]] = None,
        on_datasource: Optional[Callable[[IndexProgress], None]] = None,
    ):
        """Build the index for ``datasource_ids`` (every datasource when None), beside the live one.

        Each datasource's new entries are written into the namespace of the
        configured embedding model, and its build pointer is switched to them
        only when all are written, so a failure keeps its previous build and
        schema version answering. Runs in flight keep the build they started
        with. The entries carry the glossary's descriptions, terms and joins as
        well as the schema's; descriptions a model drafts are proposals
        (``engine.knowledge.profile``) and enter nothing until accepted.
        ``on_progress`` gets a sentence before each step;
        ``on_datasource`` gets an :class:`IndexProgress` as each datasource
        starts, is done (with its entry counts) or fails (with the reason).

        Returns:
            A ``RebuildResult``: ``ok``, ``stats``, ``empty`` and ``errors``.
        """
        from nl2sql.indexing.rebuild import rebuild_index

        return rebuild_index(
            self._ctx,
            datasource_ids=list(datasource_ids) if datasource_ids is not None else None,
            on_progress=on_progress,
            on_datasource=on_datasource,
        )

    def refresh(
        self,
        datasource_ids: Optional[Iterable[str]] = None,
        *,
        on_progress: Optional[Callable[[str], None]] = None,
        on_datasource: Optional[Callable[[IndexProgress], None]] = None,
    ):
        """Write the entries of ``datasource_ids`` (every datasource when None) again after a glossary or ``curated.yaml`` change.

        Reads no schema and calls no model: each entry's text is rendered from
        the live build's stored snapshot, the current verified queries and the
        current knowledge, and only the entries whose text changed are
        embedded. The build pointer keeps its schema version and records the
        new knowledge hash, so health stops reporting the index ``stale``. A
        datasource that has never been indexed fails with the command to run.

        Returns:
            A ``RebuildResult``, as :meth:`build` does.
        """
        from nl2sql.indexing.rebuild import rebuild_index

        return rebuild_index(
            self._ctx,
            datasource_ids=list(datasource_ids) if datasource_ids is not None else None,
            refresh=True,
            on_progress=on_progress,
            on_datasource=on_datasource,
        )

    def health(self) -> Dict[str, Any]:
        """Health of the vector index of the configured embedding model.

        ``status`` (``ok``, ``empty``, ``stale`` or ``missing``; a glossary
        changed since the build makes it ``stale``), entry counts by type, when it was built, the embedding model, the namespace, one
        entry per registered datasource, any problems found, and ``warnings``
        that leave ``status`` alone (a ``curated.yaml`` changed since it was
        indexed, a preferred table missing from the indexed schema, a
        datasource that failed to start).
        """
        from nl2sql.indexing.curated import curated_hashes
        from nl2sql.indexing.health import index_health
        from nl2sql.knowledge.merge import Knowledge

        ctx = self._ctx
        registry = ctx.ds_registry
        down = [f"Datasource '{ds}' is unavailable: {reason}" for ds, reason in registry.unavailable().items()]
        ids = registry.list_ids()
        info = ctx.embedding_info
        knowledge = getattr(ctx, "knowledge", None)
        health = index_health(
            ctx.index, ctx.namespace, ctx.state, ids,
            embedding_model=f"{info.provider}/{info.model}",
            curated_hashes=curated_hashes(getattr(ctx, "curated", None), ids),
            preferred_tables={ds: registry.get_preferred_tables(ds) for ds in ids},
            knowledge_digests={ds: knowledge.digest(ds) for ds in ids} if isinstance(knowledge, Knowledge) else None,
        )
        health.warnings.extend(down)
        return health.to_dict()

    def check(self, datasource_ids: Optional[Iterable[str]] = None, *,
              user_context: UserContext) -> List["DriftReport"]:
        """Compare each datasource's live database with the snapshot its live build was made from.

        The database is reflected with the datasource's ``index`` scope and no
        profiling (``profile: catalog``). Equal fingerprints are
        ``unchanged``; otherwise the report is ``drifted`` with the diff and
        its impact (verified queries, policy rules and preferred tables it
        breaks), and ``schema.drifted`` is published on the engine's bus. A
        datasource with no build is ``not_indexed``; one that could not be
        read is ``failed`` with the (redacted) error. Nothing is stored and
        nothing is rebuilt: drift is measured, not acted on.

        ``user_context`` is required. The caller sees drift only in the
        tables its scope may read, so drift only in denied tables is
        ``unchanged`` for it, and a datasource it cannot reach is left out
        (``datasource_ids=None``) or unknown (named).

        Raises:
            ValueError: a named datasource is not configured, or the caller
                cannot reach it (the same message, so neither is told apart).
        """
        from nl2sql.schema.drift import check

        configured = [d.id for d in self._ctx.config.datasources]
        if datasource_ids is None:
            scopes = [(ds, self._ctx.policy_engine.scope(user_context, ds)) for ds in configured]
            return [check(self._ctx, ds, scope) for ds, scope in scopes if scope.reaches_datasource()]
        return [check(self._ctx, ds, self._scope(ds, user_context)) for ds in datasource_ids]

    def versions(self, datasource_id: str, *, user_context: UserContext) -> List["SchemaVersionInfo"]:
        """Each stored schema version of the datasource, newest first.

        ``schema_version``, its structural ``fingerprint``, ``live`` (the live
        build pointer names it) and ``has_diff`` (a build stored the diff that
        introduced it, with something in it the caller may read).

        Raises:
            ValueError: the datasource is not configured or the caller cannot reach it.
        """
        from nl2sql.schema.drift import versions

        return versions(self._ctx, datasource_id, self._scope(datasource_id, user_context))

    def diff(self, datasource_id: str, schema_version: Optional[str] = None, *,
             user_context: UserContext) -> Optional["SchemaDiff"]:
        """The stored diff that introduced ``schema_version`` (the live one when None), as the caller may see it.

        None for a first build, a version whose structure did not change, or
        a diff only in tables the caller's scope denies.

        Raises:
            ValueError: the datasource is not configured or the caller cannot reach it.
        """
        from nl2sql.schema.drift import stored_diff

        return stored_diff(self._ctx, datasource_id, schema_version, self._scope(datasource_id, user_context))

    def _scope(self, datasource_id: str, user_context: UserContext) -> "Scope":
        """The caller's scope in a configured datasource it can reach; otherwise "unknown", whichever it is."""
        scope = self._ctx.policy_engine.scope(user_context, datasource_id)
        if datasource_id not in {d.id for d in self._ctx.config.datasources} or not scope.reaches_datasource():
            raise ValueError(f"Unknown datasource ID: {datasource_id}")
        return scope

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
