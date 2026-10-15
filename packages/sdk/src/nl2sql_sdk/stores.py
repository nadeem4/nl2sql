"""The four store interfaces: where the engine keeps its index, state, traces and results.

A store is named by ``type`` in one of the four ``storage`` sub-blocks, for
example ``index: {type: qdrant, url: ...}``. Every store class has:

* ``interface_version``: the version of these interfaces it was written for
  (``2``). The engine accepts exactly the version it was built for. Version 2
  added ``StateStore.put_schema_diff`` and ``get_schema_diff``; the version is
  one per kind, so every store class declares it, whatever its slot.
* ``slot``: the sub-block it fills, ``"index"``, ``"state"``, ``"traces"`` or
  ``"results"``. Store names share one entry point group, ``nl2sql.stores``,
  and the engine refuses a store in the wrong slot.
* ``config_model``: a pydantic model for the sub-block's keys apart from
  ``type``. Set ``extra="forbid"`` so a misspelt key is an error.
* ``from_config(cfg, *, folder)``: builds the store from a validated
  ``config_model`` instance, after the engine has resolved any
  ``${provider:key}`` in it. ``folder`` is ``storage.path``. It should not
  write anything: ``nl2sql config check`` builds the stores too.

This module imports only pydantic and the standard library. See
docs/architecture/phase2.md, section 4.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import (
    BinaryIO,
    ClassVar,
    ContextManager,
    Dict,
    Iterable,
    List,
    Literal,
    Optional,
    Protocol,
    Self,
    Sequence,
    Set,
    Union,
)

from pydantic import BaseModel, ConfigDict, Field

from .schema import SchemaDiff, SchemaSnapshot

Slot = Literal["index", "state", "traces", "results"]

Scalar = Union[str, int, float, bool]
"""A metadata value an index entry carries and a ``Filter`` compares."""


class Entry(BaseModel):
    """One index entry. The engine embeds ``text``; the store keeps the vector it is handed."""

    id: str
    text: str
    vector: List[float]
    metadata: Dict[str, Scalar] = Field(default_factory=dict)


class Hit(BaseModel):
    """One ``IndexStore.query`` result. ``vector`` is set only when ``with_vectors`` was asked for."""

    id: str
    text: str
    metadata: Dict[str, Scalar] = Field(default_factory=dict)
    distance: float
    vector: Optional[List[float]] = None


class Filter(BaseModel):
    """What the engine filters entries on, and nothing more: every condition must hold.

    ``eq`` and ``ne`` compare one metadata key with a value; ``in`` (``in_`` in
    Python) keeps entries whose key holds one of the values.
    """

    model_config = ConfigDict(populate_by_name=True)

    eq: Dict[str, Scalar] = Field(default_factory=dict)
    ne: Dict[str, Scalar] = Field(default_factory=dict)
    in_: Dict[str, List[Scalar]] = Field(default_factory=dict, alias="in")


class BuildPointer(BaseModel):
    """Which build of one datasource is live. Switching the pointer is the commit."""

    build_id: str
    schema_version: str
    built_at: datetime
    curated_hash: Optional[str] = None


class Store(Protocol):
    """What every store class has, whatever its slot."""

    interface_version: ClassVar[int]
    slot: ClassVar[Slot]
    config_model: ClassVar[type[BaseModel]]

    @classmethod
    def from_config(cls, cfg: BaseModel, *, folder: Path) -> Self:
        """Build the store from a validated ``config_model`` instance. ``folder`` is ``storage.path``."""
        ...


class IndexStore(Store, Protocol):
    """The vector index: entries in namespaces (one per embedding model), and each datasource's live build."""

    slot: ClassVar[Literal["index"]]

    def open(self, namespace: str, dimension: int) -> None:
        """Create ``namespace`` for vectors of ``dimension`` if it does not exist."""
        ...

    def add(self, namespace: str, entries: Sequence[Entry]) -> None: ...

    def query(self, namespace: str, vector: Sequence[float], k: int, where: Optional[Filter] = None,
              with_vectors: bool = False) -> List[Hit]:
        """The ``k`` entries nearest ``vector``. MMR, in the engine, asks for the vectors."""
        ...

    def get(self, namespace: str, where: Filter, with_vectors: bool = False) -> List[Entry]: ...

    def delete(self, namespace: str, where: Filter) -> int:
        """Delete the matching entries and return how many there were."""
        ...

    def pointers(self, namespace: str) -> Dict[str, BuildPointer]:
        """The live build of each datasource, by datasource id."""
        ...

    def set_pointers(self, namespace: str, updates: Dict[str, Optional[BuildPointer]]) -> None:
        """Set (or, with ``None``, remove) several pointers in one atomic step."""
        ...

    def namespaces(self) -> List[str]: ...

    def drop(self, namespace: str) -> None: ...

    def close(self) -> None: ...


class StateStore(Store, Protocol):
    """Schema snapshots and their diffs, the plan cache, feedback, migrations and a lock."""

    slot: ClassVar[Literal["state"]]

    def migrate(self) -> List[str]:
        """Apply any pending migrations; return their names."""
        ...

    def put_snapshot(self, snapshot: SchemaSnapshot) -> str:
        """Store an immutable snapshot; return its ``schema_version``.

        A stored snapshot is never changed: new content gets a new version,
        and content already stored returns its existing version.
        """
        ...

    def get_snapshot(self, datasource_id: str, schema_version: str) -> Optional[SchemaSnapshot]: ...

    def list_versions(self, datasource_id: str) -> List[str]:
        """The datasource's stored snapshot versions, newest first."""
        ...

    def keep_snapshots(self, datasource_id: str, keep: Set[str]) -> int:
        """Delete every snapshot of ``datasource_id`` whose version is not in ``keep``, and the plans
        cached on them and their diffs; return how many snapshots."""
        ...

    def put_schema_diff(self, diff: SchemaDiff) -> None:
        """Store the diff that introduced ``diff.to_version``, keyed by datasource and that version.

        A diff stored for the same version before is replaced. A diff with no
        ``to_version`` (one against the live database) is refused with ``ValueError``.
        """
        ...

    def get_schema_diff(self, datasource_id: str, schema_version: str) -> Optional[SchemaDiff]:
        """The diff that introduced ``schema_version``; None when none was stored (a first build, or
        a build whose structure did not change)."""
        ...

    def get_plan(self, key: str, datasource_id: str, schema_version: str) -> Optional[str]: ...

    def put_plan(self, key: str, datasource_id: str, schema_version: str, plan_json: str) -> None: ...

    def clear_plans(self) -> int: ...

    def add_feedback(self, row: dict) -> dict: ...

    def list_feedback(self, limit: Optional[int] = None) -> List[dict]: ...

    def clear_feedback(self) -> int: ...

    def lock(self, name: str, ttl_s: int) -> ContextManager[None]:
        """A lease on ``name``, held for the ``with`` block. It expires after ``ttl_s`` if its holder dies.

        Raises when another holder has the lease; the message says until when.
        """
        ...

    def close(self) -> None: ...


class TraceStore(Store, Protocol):
    """Run trace documents."""

    slot: ClassVar[Literal["traces"]]

    def write(self, trace_id: str, document: dict) -> None: ...

    def get(self, trace_id: str) -> Optional[dict]: ...

    def purge(self, older_than: datetime) -> int:
        """Delete the traces written before ``older_than``; return how many."""
        ...


class ResultStore(Store, Protocol):
    """Result files, as bytes: the engine writes the Parquet and the manifest, the store keeps them."""

    slot: ClassVar[Literal["results"]]

    def write(self, key: str, chunks: Iterable[bytes]) -> int:
        """Store the bytes under ``key``; return how many were written."""
        ...

    def open(self, key: str) -> BinaryIO: ...

    def exists(self, key: str) -> bool: ...

    def purge(self, older_than: datetime) -> int:
        """Delete the results written before ``older_than``; return how many."""
        ...


__all__ = [
    "Slot",
    "Scalar",
    "Entry",
    "Hit",
    "Filter",
    "BuildPointer",
    "Store",
    "IndexStore",
    "StateStore",
    "TraceStore",
    "ResultStore",
]
