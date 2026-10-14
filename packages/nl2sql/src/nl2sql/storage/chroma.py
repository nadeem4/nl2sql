"""The ``chroma`` index store: one Chroma collection per namespace in ``<storage.path>/index/``.

Built on Chroma's public client API only. The engine embeds and hands the
store vectors, so nothing here knows about embedding models or LangChain.
Each namespace's collection records the vector ``dimension`` it was opened
with and, as ``pointer:<datasource id>``, each datasource's live
``BuildPointer``. Chroma replaces a collection's metadata as a whole, so
``set_pointers`` writes every change in one ``modify``: that write is the
commit. See docs/architecture/phase2.md, section 4.

Reading a namespace or a folder that does not exist creates nothing.
"""
from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any, ClassVar, Dict, List, Optional, Sequence

from nl2sql.storage import NoKeys
from nl2sql_sdk.stores import BuildPointer, Entry, Filter, Hit

POINTER_KEY = "pointer:"
DIMENSION_KEY = "dimension"


class ChromaIndex:
    interface_version: ClassVar[int] = 1
    slot: ClassVar[str] = "index"
    config_model: ClassVar[type] = NoKeys

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._client: Any = None
        self._lock = RLock()

    @classmethod
    def from_config(cls, cfg: NoKeys, *, folder: Path) -> "ChromaIndex":
        return cls(Path(folder) / "index")

    # ------------------------------------------------------------ the client

    def _chroma(self, create: bool) -> Any:
        """The client, opened on first use. None when the folder is missing and ``create`` is False."""
        with self._lock:
            if self._client is None:
                if not create and not self.directory.is_dir():
                    return None
                import chromadb
                from chromadb.config import Settings

                self.directory.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(self.directory), settings=Settings(anonymized_telemetry=False))
            return self._client

    def _collection(self, namespace: str) -> Any:
        """The namespace's collection, re-read so another writer's pointers show; None if it does not exist."""
        client = self._chroma(create=False)
        if client is None:
            return None
        from chromadb.errors import NotFoundError

        try:
            return client.get_collection(namespace)
        except (NotFoundError, ValueError):
            return None

    # ------------------------------------------------------------ IndexStore

    def open(self, namespace: str, dimension: int) -> None:
        """Creates the namespace for vectors of ``dimension``, or checks the one that exists.

        Raises:
            ValueError: the namespace holds vectors of another dimension.
        """
        collection = self._chroma(create=True).get_or_create_collection(
            namespace, metadata={DIMENSION_KEY: int(dimension)})
        recorded = (collection.metadata or {}).get(DIMENSION_KEY)
        if recorded is not None and int(recorded) != int(dimension):
            raise ValueError(f"Index namespace '{namespace}' holds {recorded}-dimensional vectors, "
                             f"not {dimension}.")

    def add(self, namespace: str, entries: Sequence[Entry]) -> None:
        if not entries:
            return
        collection = self._collection(namespace)
        if collection is None:
            raise LookupError(f"Index namespace '{namespace}' is not open.")
        collection.add(
            ids=[e.id for e in entries],
            embeddings=[list(e.vector) for e in entries],
            documents=[e.text for e in entries],
            metadatas=[dict(e.metadata) or None for e in entries],
        )

    def query(self, namespace: str, vector: Sequence[float], k: int, where: Optional[Filter] = None,
              with_vectors: bool = False) -> List[Hit]:
        collection = self._collection(namespace)
        clause = _where(where)
        if collection is None or k <= 0 or clause is _NOTHING or collection.count() == 0:
            return []
        include = ["documents", "metadatas", "distances"] + (["embeddings"] if with_vectors else [])
        got = collection.query(query_embeddings=[list(vector)], n_results=k, where=clause, include=include)
        ids = _first(got, "ids")
        texts = _first(got, "documents")
        metadatas = _first(got, "metadatas")
        distances = _first(got, "distances")
        vectors = _first(got, "embeddings") if with_vectors else []
        return [
            Hit(id=entry_id, text=texts[i] or "", metadata=dict(metadatas[i] or {}),
                distance=float(distances[i]),
                vector=[float(v) for v in vectors[i]] if with_vectors else None)
            for i, entry_id in enumerate(ids)
        ]

    def get(self, namespace: str, where: Filter, with_vectors: bool = False) -> List[Entry]:
        collection = self._collection(namespace)
        clause = _where(where)
        if collection is None or clause is _NOTHING:
            return []
        include = ["documents", "metadatas"] + (["embeddings"] if with_vectors else [])
        got = collection.get(where=clause, include=include)
        ids = list(got.get("ids") or [])
        texts = list(got.get("documents") or [None] * len(ids))
        metadatas = list(got.get("metadatas") or [None] * len(ids))
        vectors = got.get("embeddings") if with_vectors else None
        return [
            Entry(id=entry_id, text=texts[i] or "", metadata=dict(metadatas[i] or {}),
                  vector=[float(v) for v in vectors[i]] if with_vectors else [])
            for i, entry_id in enumerate(ids)
        ]

    def delete(self, namespace: str, where: Filter) -> int:
        collection = self._collection(namespace)
        clause = _where(where)
        if collection is None or clause is _NOTHING:
            return 0
        ids = list(collection.get(where=clause, include=[]).get("ids") or [])
        if ids:
            collection.delete(ids=ids)
        return len(ids)

    def pointers(self, namespace: str) -> Dict[str, BuildPointer]:
        collection = self._collection(namespace)
        if collection is None:
            return {}
        return {key[len(POINTER_KEY):]: BuildPointer.model_validate_json(value)
                for key, value in (collection.metadata or {}).items() if key.startswith(POINTER_KEY)}

    def set_pointers(self, namespace: str, updates: Dict[str, Optional[BuildPointer]]) -> None:
        """Sets and removes pointers in one metadata write, under this store's lock.

        Raises:
            LookupError: the namespace is not open.
        """
        with self._lock:
            collection = self._collection(namespace)
            if collection is None:
                raise LookupError(f"Index namespace '{namespace}' is not open.")
            metadata = dict(collection.metadata or {})
            for datasource_id, pointer in updates.items():
                key = f"{POINTER_KEY}{datasource_id}"
                if pointer is None:
                    metadata.pop(key, None)
                else:
                    metadata[key] = pointer.model_dump_json()
            collection.modify(metadata=metadata)

    def namespaces(self) -> List[str]:
        client = self._chroma(create=False)
        if client is None:
            return []
        return sorted(c.name for c in client.list_collections())

    def drop(self, namespace: str) -> None:
        client = self._chroma(create=False)
        if client is not None and namespace in self.namespaces():
            client.delete_collection(namespace)

    def close(self) -> None:
        """Releases the Chroma client. Chroma counts its clients per folder, so other stores stay open."""
        with self._lock:
            client, self._client = self._client, None
        close = getattr(client, "close", None)
        if callable(close):
            close()


# A filter that can match nothing (an empty ``in``): Chroma refuses an empty list.
_NOTHING = object()


def _where(where: Optional[Filter]) -> Any:
    """``where`` as a Chroma ``where`` clause: None for no condition, ``_NOTHING`` when none can hold."""
    if where is None:
        return None
    clauses: List[Dict[str, Any]] = [{key: value} for key, value in where.eq.items()]
    clauses += [{key: {"$ne": value}} for key, value in where.ne.items()]
    for key, values in where.in_.items():
        if not values:
            return _NOTHING
        clauses.append({key: {"$in": list(values)}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _first(results: Dict[str, Any], key: str) -> List[Any]:
    rows = results.get(key)
    if rows is None or len(rows) == 0 or rows[0] is None:
        return []
    return list(rows[0])
