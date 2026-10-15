"""Schema drift, measured: the live database against its build, and the stored versions (D1, D31).

docs/architecture/wave_a.md, section 10. Nothing here re-indexes, schedules
or writes the index: the owner wants drift measured before anything acts on
it. Everything a caller gets back has been through its ``Scope``, so a denied
table's name never reaches it; ``schema.drifted`` is the operator's event, on
the engine's bus with the whole diff's counts and no table names.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Literal, Optional

from pydantic import BaseModel

from nl2sql.schema.diff import diff_contracts, visible_diff
from nl2sql.schema.fingerprint import generate_schema_fingerprint
from nl2sql.schema.impact import DriftImpact, drift_impact
from nl2sql_sdk.schema import SchemaDiff

if TYPE_CHECKING:
    from nl2sql.policies import Scope
    from nl2sql.runtime import Runtime


class DriftReport(BaseModel):
    """One datasource's check: its live database against the snapshot its live build was made from."""

    datasource_id: str
    status: Literal["unchanged", "drifted", "not_indexed", "failed"]
    live_version: Optional[str] = None   # the build pointer's schema version
    checked_at: datetime
    diff: Optional[SchemaDiff] = None    # live database against the live build, as the caller may see it
    impact: Optional[DriftImpact] = None
    error: Optional[str] = None          # redacted


class SchemaVersionInfo(BaseModel):
    """One stored schema version of a datasource, as ``engine.index.versions`` lists it."""

    schema_version: str
    fingerprint: str
    live: bool       # the live build pointer names it
    has_diff: bool   # a diff introduced it, with something in it the caller may see


def check(ctx: "Runtime", datasource_id: str, scope: "Scope") -> DriftReport:
    """Reflects the live database (catalog only) and compares it with the live build's snapshot.

    Equal fingerprints are ``unchanged``. Otherwise the diff is computed,
    ``schema.drifted`` is published (``detected_by: "check"``), and the
    report carries the part of the diff ``scope`` may read, and its impact.
    Drift only in tables the scope denies is ``unchanged`` for this caller.
    Nothing is stored and nothing is rebuilt.
    """
    from nl2sql.tracing.trace import redact_for

    now = datetime.now(timezone.utc)
    pointer = ctx.index.pointers(ctx.namespace).get(datasource_id)
    if pointer is None:
        return DriftReport(datasource_id=datasource_id, status="not_indexed", checked_at=now)
    report = DriftReport(datasource_id=datasource_id, status="failed", live_version=pointer.schema_version,
                         checked_at=now)
    registry = ctx.ds_registry
    try:
        adapter = registry.get_adapter(datasource_id)
        index_scope = registry.get_index_scope(datasource_id).model_copy(
            update={"profile": "catalog", "sample_values": False})
        live = adapter.fetch_schema_snapshot(index_scope)
        stored = ctx.state.get_snapshot(datasource_id, pointer.schema_version)
    except Exception as exc:  # a driver error, an unavailable datasource: reported, never raised
        report.error = redact_for(ctx, f"{type(exc).__name__}: {exc}")
        return report
    if stored is None:
        report.error = (f"The snapshot of the live build ({pointer.schema_version}) is missing from the state "
                        f"store; run `nl2sql index --datasource {datasource_id}`.")
        return report

    report.status = "unchanged"
    if generate_schema_fingerprint(live.contract) == generate_schema_fingerprint(stored.contract):
        return report
    diff = diff_contracts(stored.contract, live.contract, from_version=pointer.schema_version, to_version=None)
    publish_drift(ctx, diff, "check")
    visible = visible_diff(diff, scope.allows)
    if visible.is_empty():
        return report
    report.status, report.diff = "drifted", visible
    report.impact = drift_impact(ctx, visible, [t.table for t in live.contract.tables.values()])
    return report


def versions(ctx: "Runtime", datasource_id: str, scope: "Scope") -> List[SchemaVersionInfo]:
    """The datasource's stored versions, newest first."""
    pointer = ctx.index.pointers(ctx.namespace).get(datasource_id)
    out = []
    for version in ctx.state.list_versions(datasource_id):
        snapshot = ctx.state.get_snapshot(datasource_id, version)
        if snapshot is None:
            continue
        out.append(SchemaVersionInfo(
            schema_version=version, fingerprint=generate_schema_fingerprint(snapshot.contract),
            live=pointer is not None and pointer.schema_version == version,
            has_diff=stored_diff(ctx, datasource_id, version, scope) is not None))
    return out


def stored_diff(ctx: "Runtime", datasource_id: str, schema_version: Optional[str],
                scope: "Scope") -> Optional[SchemaDiff]:
    """The stored diff that introduced ``schema_version`` (the live one when None), as ``scope`` may see it.

    None when there is none, or nothing in it the scope may read.
    """
    if schema_version is None:
        pointer = ctx.index.pointers(ctx.namespace).get(datasource_id)
        if pointer is None:
            return None
        schema_version = pointer.schema_version
    diff = ctx.state.get_schema_diff(datasource_id, schema_version)
    if diff is None:
        return None
    visible = visible_diff(diff, scope.allows)
    return None if visible.is_empty() else visible


def publish_drift(ctx: "Runtime", diff: SchemaDiff, detected_by: Literal["check", "build"]) -> None:
    """``schema.drifted`` on the engine's bus: the versions and the whole diff's counts, never a table name."""
    ctx.bus.publish("schema.drifted", {"datasource_id": diff.datasource_id, "from_version": diff.from_version,
                                       "to_version": diff.to_version, "detected_by": detected_by,
                                       "counts": diff.counts()}, tenant_id=ctx.tenant_id)
