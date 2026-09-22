"""Index health: what the vector index holds, and whether it matches the snapshot.

Health is read from the index's contents, never from whether its folder
exists: the demo once had a ``data/vector_store_demo`` folder holding 0
entries, and every question failed at the resolver while the schema panel,
which reads the separate snapshot store, looked fine.

Status, worst first:

``missing``  no index folder or no collection
``empty``    the collection holds no entries
``stale``    a datasource has no entries, or its entries were built from an
             older schema version than the latest snapshot
``ok``       everything else
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from nl2sql.common.logger import get_logger
from nl2sql.common.settings import settings
from nl2sql.schema.store import build_schema_store

logger = get_logger(__name__)

REINDEX_HINT = "Re-index with `nl2sql index`."


@dataclass
class DatasourceIndexHealth:
    datasource_id: str
    entries: int
    index_version: Optional[str]
    snapshot_version: Optional[str]
    built_at: Optional[str] = None


@dataclass
class IndexHealth:
    status: str
    total: int = 0
    counts: Dict[str, int] = field(default_factory=dict)
    built_at: Optional[str] = None
    embedding_model: Optional[str] = None
    datasources: List[DatasourceIndexHealth] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


BUILD_KEY = "build:"
BUILT_AT_KEY = "built_at:"
MODEL_KEY = "embedding_model"


def _keyed(collection_metadata: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    return {k[len(prefix):]: v for k, v in collection_metadata.items() if k.startswith(prefix)}


def summarize(
    metadatas: Iterable[Dict[str, Any]],
    latest_version: Callable[[str], Optional[str]],
    datasource_ids: Iterable[str],
    collection_metadata: Optional[Dict[str, Any]] = None,
    configured_model: Optional[str] = None,
) -> IndexHealth:
    """Judges an index from its entries' metadata and the snapshot store.

    Only each datasource's active build is counted (see
    ``VectorStore.refresh_schema_chunks``): entries of a build that was never
    switched in, or of a replaced one not yet deleted, are not what readers see.
    """
    collection_metadata = dict(collection_metadata or {})
    builds = _keyed(collection_metadata, BUILD_KEY)
    built = _keyed(collection_metadata, BUILT_AT_KEY)
    recorded_model = collection_metadata.get(MODEL_KEY)
    metadatas = [
        md for md in (m or {} for m in metadatas)
        if md.get("datasource_id") not in builds or md.get("build_id") == builds[md.get("datasource_id")]
    ]
    counts: Dict[str, int] = {}
    per_ds: Dict[str, int] = {}
    versions: Dict[str, str] = {}
    total = 0
    for md in metadatas:
        md = md or {}
        total += 1
        kind = md.get("type", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
        ds_id = md.get("datasource_id")
        if ds_id:
            per_ds[ds_id] = per_ds.get(ds_id, 0) + 1
            # The datasource entry carries the version the whole set was built
            # from; any entry will do when it is missing.
            if kind == "schema.datasource" or ds_id not in versions:
                if md.get("schema_version"):
                    versions[ds_id] = md["schema_version"]

    ids = list(dict.fromkeys([*datasource_ids, *per_ds.keys()]))
    datasources = [
        DatasourceIndexHealth(
            datasource_id=ds_id,
            entries=per_ds.get(ds_id, 0),
            index_version=versions.get(ds_id),
            snapshot_version=latest_version(ds_id),
            built_at=built.get(ds_id),
        )
        for ds_id in ids
    ]

    health = IndexHealth(
        status="ok", total=total, counts=dict(sorted(counts.items())),
        built_at=max(built.values()) if built else None,
        embedding_model=recorded_model, datasources=datasources,
    )
    if total == 0:
        health.status = "empty"
        health.problems.append(
            "The vector index is empty, so no question can be matched to a datasource. " + REINDEX_HINT
        )
        return health

    if configured_model and recorded_model and configured_model != recorded_model:
        health.problems.append(
            f"The index was built with the embedding model '{recorded_model}', but '{configured_model}' "
            "is configured. A model change needs a full rebuild: run `nl2sql index --full`."
        )
    for ds in datasources:
        if ds.entries == 0:
            health.problems.append(f"Datasource '{ds.datasource_id}' has no entries in the vector index.")
        elif ds.snapshot_version and ds.index_version != ds.snapshot_version:
            health.problems.append(
                f"Datasource '{ds.datasource_id}' was indexed from schema version {ds.index_version}, "
                f"but the latest snapshot is {ds.snapshot_version}."
            )
    if health.problems:
        health.status = "stale"
        if not any("--full" in p for p in health.problems):
            health.problems.append(REINDEX_HINT)
    return health


def inspect_vector_store(vector_store, schema_store, datasource_ids: Iterable[str]) -> IndexHealth:
    """Health of a live :class:`VectorStore`, as the engine sees it."""
    try:
        metadatas = vector_store.entry_metadatas(active_only=False)
        collection_metadata = vector_store._live_metadata()
    except Exception as exc:
        logger.warning(f"Could not read the vector index: {exc}")
        return IndexHealth(status="missing", problems=[f"The vector index could not be read: {exc}. {REINDEX_HINT}"])
    latest = schema_store.get_latest_version if schema_store is not None else (lambda _ds: None)
    return summarize(
        metadatas, latest, datasource_ids,
        collection_metadata=collection_metadata,
        configured_model=vector_store.embedding_model_id(),
    )


def inspect_index_at(
    persist_directory: Path,
    collection_name: str,
    schema_store_path: Path,
    datasource_ids: Iterable[str],
) -> IndexHealth:
    """Health of an index on disk, without loading an embedding model.

    Creates nothing: a missing folder, collection or snapshot file is reported,
    not made.
    """
    persist_directory = Path(persist_directory).resolve()
    if not persist_directory.is_dir():
        return IndexHealth(status="missing", problems=[f"No vector index at {persist_directory}. {REINDEX_HINT}"])

    import chromadb

    try:
        collection = chromadb.PersistentClient(path=str(persist_directory)).get_collection(collection_name)
        metadatas = collection.get(include=["metadatas"])["metadatas"] or []
        collection_metadata = dict(collection.metadata or {})
    except Exception as exc:
        return IndexHealth(
            status="missing",
            problems=[f"No collection '{collection_name}' in {persist_directory} ({exc}). {REINDEX_HINT}"],
        )

    versions: Dict[str, Optional[str]] = {}
    schema_store_path = Path(schema_store_path)
    if schema_store_path.is_file():
        store = build_schema_store(settings.schema_store_backend, settings.schema_store_max_versions,
                                   path=schema_store_path)
        try:
            ids = set(datasource_ids) | {md.get("datasource_id") for md in metadatas if md}
            versions = {ds: store.get_latest_version(ds) for ds in ids if ds}
        finally:
            close = getattr(store, "close", None)
            if close:
                close()

    return summarize(metadatas, lambda ds: versions.get(ds), datasource_ids,
                     collection_metadata=collection_metadata)
