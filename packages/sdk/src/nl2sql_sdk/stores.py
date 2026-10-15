"""The four store interfaces: where the engine keeps its index, state, traces and results.

A store is named by ``type`` in one of the four ``storage`` sub-blocks, for
example ``index: {type: qdrant, url: ...}``. Every store class has:

* ``interface_version``: the version of these interfaces it was written for
  (``3``). The engine accepts exactly the version it was built for. Version 2
  added ``StateStore.put_schema_diff`` and ``get_schema_diff``; version 3 the
  review queue's methods (``put_proposal``, ``get_proposal``,
  ``list_proposals``, ``decide_proposal``, ``purge_proposals``, typed with
  ``Proposal``) and ``BuildPointer.knowledge_hash``. The version is one per
  kind, so every store class declares it, whatever its slot.
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
docs/architecture/phase2.md, section 4, and, for proposals,
docs/architecture/wave_b.md, section 5.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
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
    # The datasource's knowledge digest the build was made with; health reports ``stale`` when it differs.
    knowledge_hash: Optional[str] = None


ProposalKind = Literal["join", "column", "table", "term", "verified_query"]
ProposalOrigin = Literal["profiler", "draft", "correction", "thumbs_up", "clarification"]
ProposalStatus = Literal["proposed", "accepted", "rejected", "superseded"]


def proposal_id(kind: str, datasource_id: str, target: str, change: Dict[str, Any]) -> str:
    """A proposal's identity: SHA-256 of its kind, datasource, target and the canonical JSON of its change, 16 hex.

    So the same change is the same row (a rejected one stays rejected) and a
    different change for the same target is a new one.
    """
    canonical = json.dumps([kind, datasource_id, target, change], sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class Proposal(BaseModel):
    """One suggested change to a person's knowledge or curated answers, waiting in the review queue.

    ``change`` holds the fields it would set, in ``glossary.yaml``'s or
    ``curated.yaml``'s shape; ``tables`` every ``datasource.schema.table`` it
    names, so a caller sees it only when their scope reaches all of them;
    ``evidence`` counts and checks, never rows. Build one with ``build``,
    which computes ``id``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: ProposalKind
    datasource_id: str
    target: str
    change: Dict[str, Any]
    tables: List[str] = Field(default_factory=list)
    origin: ProposalOrigin
    evidence: Dict[str, Any] = Field(default_factory=dict)
    status: ProposalStatus = "proposed"
    schema_version: Optional[str] = None
    created_at: datetime
    created_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    reason: Optional[str] = None
    wrote: Optional[str] = None

    @classmethod
    def build(cls, *, kind: ProposalKind, datasource_id: str, target: str, change: Dict[str, Any],
              origin: ProposalOrigin, tables: Sequence[str] = (), evidence: Optional[Dict[str, Any]] = None,
              schema_version: Optional[str] = None, created_by: Optional[str] = None,
              created_at: Optional[datetime] = None) -> "Proposal":
        """A new ``proposed`` row, its ``id`` hashed from its kind, datasource, target and change."""
        return cls(id=proposal_id(kind, datasource_id, target, change), kind=kind, datasource_id=datasource_id,
                   target=target, change=change, tables=list(tables), origin=origin, evidence=evidence or {},
                   schema_version=schema_version, created_by=created_by,
                   created_at=created_at or datetime.now(timezone.utc))


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
    """Schema snapshots and their diffs, the plan cache, feedback, the review queue's proposals, migrations
    and a lock."""

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

    def put_proposal(self, proposal: Proposal) -> None:
        """Store ``proposal``, replacing the row with its ``id`` if there is one."""
        ...

    def get_proposal(self, proposal_id: str) -> Optional[Proposal]: ...

    def list_proposals(self, kind: Optional[str] = None, status: Optional[str] = None,
                       datasource_id: Optional[str] = None, limit: Optional[int] = None) -> List[Proposal]:
        """The proposals matching every filter given, newest (``created_at``) first."""
        ...

    def decide_proposal(self, proposal_id: str, status: ProposalStatus, *, by: Optional[str],
                        reason: Optional[str] = None, wrote: Optional[str] = None,
                        at: Optional[datetime] = None) -> Optional[Proposal]:
        """Set the proposal's ``status``, ``decided_by``, ``decided_at`` (``at``, else now), ``reason`` and
        ``wrote``; return it, or None when there is no such id."""
        ...

    def purge_proposals(self, older_than: datetime) -> int:
        """Delete the decided proposals (not ``proposed``) decided before ``older_than``; return how many."""
        ...

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
    "ProposalKind",
    "ProposalOrigin",
    "ProposalStatus",
    "proposal_id",
    "Proposal",
    "Store",
    "IndexStore",
    "StateStore",
    "TraceStore",
    "ResultStore",
]
