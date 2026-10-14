"""Index health: what the live builds hold, read through the index store.

Health is read from the index's contents, never from whether its folder
exists: the demo once had a ``data/vector_store_demo`` folder holding 0
entries, and every question failed at the resolver while the schema panel,
which reads the separate snapshot store, looked fine.

Only the builds the pointers name are counted: entries of a build that was
never switched in, or of a replaced one kept for runs in flight, are not what
a new run reads.

Status, worst first:

``missing``  the configured embedding model has no namespace yet (never
             indexed, or the model changed)
``empty``    the namespace has no live entries
``stale``    a datasource has no live build, or its build's schema snapshot
             is gone from the state store
``ok``       everything else

Warnings never change the status. One is a ``curated.yaml`` edited since the
datasource was indexed: its verified query entries are stale, but they still
point at queries that exist, so questions keep working. Another is a
``preferred_tables`` entry missing from the indexed schema: there is nothing to
rank first, and every other table still answers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from nl2sql.common.logger import get_logger
from nl2sql_sdk.stores import Filter

logger = get_logger(__name__)

REINDEX_HINT = "Re-index with `nl2sql index`."


@dataclass
class DatasourceIndexHealth:
    datasource_id: str
    entries: int
    index_version: Optional[str]  # the schema version the live build was made from
    built_at: Optional[str] = None


@dataclass
class IndexHealth:
    status: str
    total: int = 0
    counts: Dict[str, int] = field(default_factory=dict)
    built_at: Optional[str] = None
    embedding_model: Optional[str] = None
    namespace: Optional[str] = None
    datasources: List[DatasourceIndexHealth] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def preferred_table_warnings(
    preferred_tables: Dict[str, List[str]],
    snapshot_of: Callable[[str], Any],
) -> List[str]:
    """One warning per ``preferred_tables`` entry missing from its datasource's live snapshot.

    ``snapshot_of`` returns the snapshot or None; a datasource with no
    snapshot is skipped, since not being indexed is reported on its own.
    """
    from nl2sql.datasources.models import matches_table

    warnings: List[str] = []
    for ds_id, names in (preferred_tables or {}).items():
        if not names:
            continue
        snapshot = snapshot_of(ds_id)
        if snapshot is None:
            continue
        refs = [contract.table for contract in snapshot.contract.tables.values()]
        for name in names:
            if not any(matches_table(name, ref) for ref in refs):
                warnings.append(
                    f"Preferred table '{name}' of '{ds_id}' is not in the indexed schema; check the name, "
                    f"or create it and run `nl2sql index --datasource {ds_id}`."
                )
    return warnings


def index_health(
    index,
    namespace: str,
    state,
    datasource_ids: Iterable[str],
    embedding_model: Optional[str] = None,
    curated_hashes: Optional[Dict[str, str]] = None,
    preferred_tables: Optional[Dict[str, List[str]]] = None,
) -> IndexHealth:
    """Judges the live builds in ``namespace`` of ``index`` against the registered datasources.

    ``curated_hashes`` maps a datasource id to the hash of its verified
    queries as the file is now (``indexing.curated.curated_hashes``). An
    indexed datasource whose build recorded another hash gets a warning; None
    compares nothing.
    """
    datasource_ids = list(datasource_ids)
    try:
        if namespace not in index.namespaces():
            model = f"'{embedding_model}'" if embedding_model else "the configured embedding model"
            return IndexHealth(
                status="missing", embedding_model=embedding_model, namespace=namespace,
                problems=[f"There is no index for {model} yet. Run `nl2sql index`."],
            )
        pointers = index.pointers(namespace)
        builds = [p.build_id for p in pointers.values()]
        entries = index.get(namespace, Filter(**{"in": {"build_id": builds}})) if builds else []
    except Exception as exc:
        logger.warning(f"Could not read the vector index: {exc}")
        return IndexHealth(status="missing", embedding_model=embedding_model, namespace=namespace,
                           problems=[f"The vector index could not be read: {exc}. {REINDEX_HINT}"])

    counts: Dict[str, int] = {}
    per_ds: Dict[str, int] = {}
    for entry in entries:
        kind = str(entry.metadata.get("type", "unknown"))
        counts[kind] = counts.get(kind, 0) + 1
        ds_id = entry.metadata.get("datasource_id")
        if ds_id:
            per_ds[ds_id] = per_ds.get(ds_id, 0) + 1

    ids = list(dict.fromkeys([*datasource_ids, *pointers.keys()]))
    datasources = [
        DatasourceIndexHealth(
            datasource_id=ds_id,
            entries=per_ds.get(ds_id, 0),
            index_version=pointers[ds_id].schema_version if ds_id in pointers else None,
            built_at=pointers[ds_id].built_at.isoformat() if ds_id in pointers else None,
        )
        for ds_id in ids
    ]
    stamps = [d.built_at for d in datasources if d.built_at]
    health = IndexHealth(
        status="ok", total=len(entries), counts=dict(sorted(counts.items())),
        built_at=max(stamps) if stamps else None, embedding_model=embedding_model,
        namespace=namespace, datasources=datasources,
    )
    if not entries:
        health.status = "empty"
        health.problems.append(
            "The vector index is empty, so no question can be matched to a datasource. " + REINDEX_HINT
        )
        return health

    def snapshot_of(ds_id: str):
        pointer = pointers.get(ds_id)
        if pointer is None or state is None:
            return None
        return state.get_snapshot(ds_id, pointer.schema_version)

    for ds in datasources:
        if ds.entries == 0:
            health.problems.append(f"Datasource '{ds.datasource_id}' has no entries in the vector index.")
        elif state is not None and snapshot_of(ds.datasource_id) is None:
            health.problems.append(
                f"Datasource '{ds.datasource_id}' was indexed from schema version {ds.index_version}, "
                "which is no longer in the state store."
            )
    if curated_hashes:
        from nl2sql.indexing.curated import curated_hash

        nothing = curated_hash(None)  # what a build without verified queries records
        for ds in datasources:
            current = curated_hashes.get(ds.datasource_id)
            recorded = pointers[ds.datasource_id].curated_hash if ds.datasource_id in pointers else None
            if current and ds.entries and (recorded or nothing) != current:
                health.warnings.append(
                    f"curated.yaml changed for '{ds.datasource_id}' since it was indexed; run "
                    f"`nl2sql index --curated-only --datasource {ds.datasource_id}`."
                )
    if preferred_tables:
        health.warnings.extend(preferred_table_warnings(preferred_tables, snapshot_of))
    if health.problems:
        health.status = "stale"
        health.problems.append(REINDEX_HINT)
    return health
