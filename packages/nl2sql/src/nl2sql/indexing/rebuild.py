"""Index builds that never leave an empty or half-built index behind.

:func:`rebuild_index` is the one entry point, used by ``nl2sql index``, the
demo's startup repair and the playground's Rebuild button. Each datasource is
built on its own (``IndexingOrchestrator``): its new entries are written
beside its live ones, and its build pointer is switched to them only when all
are written. A failure keeps that datasource's previous build and schema
version live, and no other datasource's entries are read, deleted or
rewritten.

Every build goes into the namespace of the configured embedding model, so a
model change needs no special rebuild: the new model's namespace starts
empty, and ``nl2sql index`` fills it (docs/architecture/phase2.md, section 4).

A build holds the state store's ``index`` lock, so ``nl2sql index`` is safe
to run while ``nl2sql-api`` serves the same ``storage.path``: a second build
started meanwhile fails at once, naming when the lease runs out. The lease
expires after ``INDEX_LOCK_TTL_S``, so a build that died cannot block forever.

Each datasource's start and failure is published on the engine's bus
(``index.started``, ``index.failed``); the orchestrator publishes the rest.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional

from nl2sql.api.types import IndexProgress
from nl2sql.common.logger import get_logger
from nl2sql.indexing.orchestrator import IndexingOrchestrator

if TYPE_CHECKING:
    from nl2sql.common.cancellation import CancellationToken

logger = get_logger("indexing_rebuild")

MODEL_STEP = "Loading the embedding model (the first run downloads about 79 MB)"

INDEX_LOCK = "index"
INDEX_LOCK_TTL_S = 3600


@dataclass
class RebuildResult:
    """What a rebuild did.

    ``ok`` is True only when every requested datasource's new entries are live. ``cancelled`` is True when
    the caller's token stopped the build between datasources: the ones before it are live, the rest untouched.
    """

    stats: List[Dict[str, Any]] = field(default_factory=list)
    empty: List[str] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)
    ok: bool = False
    cancelled: bool = False


def rebuild_index(
    ctx,
    datasource_ids: Optional[Iterable[str]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    on_datasource: Optional[Callable[["IndexProgress"], None]] = None,
    refresh: bool = False,
    cancellation_token: Optional["CancellationToken"] = None,
) -> RebuildResult:
    """Builds the index of each datasource, and switches each one's pointer as it succeeds.

    Args:
        ctx: The engine's ``Runtime``.
        datasource_ids: Only these datasources; all registered ones when None.
        on_progress: Called with a sentence before each step.
        on_datasource: Called with an :class:`~nl2sql.api.types.IndexProgress`
            as each datasource starts, is done (with its entry counts) or
            fails (with the reason).
        refresh: Write each datasource's entries again from its stored
            snapshot, the verified queries and the knowledge, with no schema
            read and no model call; only changed entries are embedded.
        cancellation_token: Checked before each datasource; once cancelled, the
            rest are left alone and the result says ``cancelled``.

    Returns:
        A :class:`RebuildResult`.
    """
    say = on_progress or (lambda _msg: None)
    told = on_datasource or (lambda _progress: None)

    def tell(progress: IndexProgress) -> None:
        _publish(ctx, progress)
        told(progress)

    result = RebuildResult()

    adapters = list(ctx.ds_registry.list_adapters())
    # Datasources that failed to start: each one in scope is an error with its
    # reason; the rest are indexed.
    unavailable = getattr(ctx.ds_registry, "unavailable", dict)()
    in_scope = datasource_ids if datasource_ids is not None else unavailable
    for ds_id in dict.fromkeys(in_scope):
        if ds_id in unavailable:
            reason = f"Datasource '{ds_id}' is unavailable: {unavailable[ds_id]}"
            result.errors.append({"datasource_id": ds_id, "error": reason})
            tell(IndexProgress(ds_id, "failed", reason=reason))
    if datasource_ids is not None:
        wanted = list(dict.fromkeys(datasource_ids))
        known = {a.datasource_id for a in adapters}
        for ds_id in wanted:
            if ds_id not in known and ds_id not in unavailable:
                result.errors.append({"datasource_id": ds_id, "error": "No such datasource is configured."})
                tell(IndexProgress(ds_id, "failed", reason="No such datasource is configured."))
        adapters = [a for a in adapters if a.datasource_id in wanted]
        if result.errors:
            return result

    with ExitStack() as held:
        try:
            held.enter_context(ctx.state.lock(INDEX_LOCK, ttl_s=INDEX_LOCK_TTL_S))
        except Exception as exc:
            reason = f"Another index build is running: {exc}"
            logger.error(reason)
            for adapter in adapters:
                result.errors.append({"datasource_id": adapter.datasource_id, "error": reason})
                tell(IndexProgress(adapter.datasource_id, "failed", reason=reason))
            return result
        _build(ctx, adapters, result, refresh=refresh, say=say, tell=tell, token=cancellation_token)

    result.ok = not result.errors and bool(result.stats) and not result.cancelled
    return result


def _build(ctx, adapters, result: RebuildResult, *, refresh: bool,
           say: Callable[[str], None], tell: Callable[["IndexProgress"], None],
           token: Optional["CancellationToken"] = None) -> None:
    say(MODEL_STEP)
    # Embeddings load lazily; warming them here puts the one slow,
    # first-run-only download under its own step.
    ctx.embedder.embed_query("warm up")

    orchestrator = IndexingOrchestrator(ctx)
    for adapter in adapters:
        if token is not None and token.is_cancelled():
            result.cancelled = True
            return
        ds_id = adapter.datasource_id
        tell(IndexProgress(ds_id, "started"))
        try:
            if refresh:
                say(f"Refreshing {ds_id}: embedding the entries that changed")
                stats = orchestrator.refresh(ds_id)
            else:
                say(f"Indexing {ds_id}: reading the schema and embedding its entries")
                stats = orchestrator.index_datasource(adapter)
        except Exception as exc:
            logger.error(f"Failed to index {ds_id}: {exc}")
            result.errors.append({"datasource_id": ds_id, "error": str(exc)})
            tell(IndexProgress(ds_id, "failed", reason=str(exc)))
            continue
        if stats:
            result.stats.append(stats)
        else:
            result.empty.append(ds_id)
        tell(IndexProgress(ds_id, "done", counts={k: v for k, v in (stats or {}).items()
                                                  if isinstance(v, int) and not isinstance(v, bool)}))


def _publish(ctx, progress: IndexProgress) -> None:
    """``index.started`` and ``index.failed`` on the engine's bus: a build runs outside any question.

    ``index.swapped`` is the orchestrator's, published when the pointer switches.
    """
    if progress.state == "started":
        ctx.bus.publish("index.started", {"datasource_id": progress.datasource_id, "namespace": ctx.namespace},
                        tenant_id=ctx.tenant_id)
    elif progress.state == "failed":
        from nl2sql.tracing.trace import redact_for

        # The reason is exception text, so it is redacted (D15).
        ctx.bus.publish("index.failed", {"datasource_id": progress.datasource_id,
                                         "reason": redact_for(ctx, progress.reason or "")}, tenant_id=ctx.tenant_id)
