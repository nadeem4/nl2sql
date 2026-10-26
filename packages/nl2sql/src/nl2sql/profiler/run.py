"""``engine.knowledge.profile``: read the live snapshot, probe a bounded number of join candidates, file proposals.

docs/architecture/wave_b.md, section 6. The run reads the live build's snapshot (it never reflects on its own)
and only the tables the caller's scope reaches (D60), so its proposals name only tables that caller can see.
Probes run in order of candidate strength, one at a time, until ``settings.profiler.max_probes`` or
``time_budget_s`` is reached; the candidates left are proposed with ``overlap: null`` (D45). Everything goes
through the review queue (D38, D41): nothing here writes a file.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Set

from nl2sql_sdk.protocols import ProfilingAdapterProtocol
from nl2sql_sdk.schema import ColumnRef, SchemaSnapshot
from nl2sql_sdk.stores import Proposal

from nl2sql.knowledge.resolve import resolve_column, resolve_join, table_label
from nl2sql.profiler.joins import JoinCandidate, find_candidates
from nl2sql.profiler.models import ProfileReport
from nl2sql.profiler.roles import find_roles
from nl2sql.review.queue import propose, table_names

if TYPE_CHECKING:
    from nl2sql.policies import UserContext


def _now() -> float:
    return time.monotonic()


def profile(ctx: Any, datasource_ids: Sequence[str], user: "UserContext") -> List[ProfileReport]:
    """One report per datasource in ``datasource_ids`` the caller reaches."""
    reports = []
    for datasource_id in datasource_ids:
        scope = ctx.policy_engine.scope(user, datasource_id)
        if not scope.reaches_datasource():
            continue
        started = _now()
        version = ctx.vector_store.schema_version(datasource_id)
        snapshot = ctx.vector_store.snapshot(datasource_id)
        if snapshot is None or version is None:
            reports.append(ProfileReport(
                datasource_id=datasource_id, schema_version="", seconds=0.0,
                issues=[f"{datasource_id} is not indexed yet, so there is no schema to profile. "
                        f"Run `nl2sql index`, then profile again."]))
            continue
        report = _profile_datasource(ctx, datasource_id, scope, snapshot, version, started)
        ctx.bus.publish("profile.finished", {
            "datasource_id": datasource_id, "proposals": report.proposals, "probes": report.probes,
            "skipped_by_budget": report.skipped_by_budget, "drafted": report.drafted,
            "stopped_by": report.stopped_by, "cost": report.cost, "seconds": report.seconds},
            tenant_id=ctx.tenant_id)
        reports.append(report)
    return reports


def _profile_datasource(ctx: Any, datasource_id: str, scope: Any, snapshot: SchemaSnapshot, version: str,
                        started: float) -> ProfileReport:
    settings = ctx.config.settings.profiler
    issues: List[str] = []
    counts: Dict[str, int] = {}
    try:
        dialect: Optional[str] = ctx.ds_registry.get_dialect(datasource_id)
        adapter = ctx.ds_registry.get_adapter(datasource_id)
    except ValueError:  # unavailable: names and types still make candidates, but nothing can be probed
        dialect, adapter = None, None
        issues.append(f"{datasource_id} is unavailable, so no overlap was probed.")
    can_probe = isinstance(adapter, ProfilingAdapterProtocol)

    knowledge = ctx.knowledge.get(datasource_id)
    known, said = _glossary_facts(knowledge, snapshot, dialect)

    def file(proposal: Proposal) -> None:
        row = propose(ctx, proposal)
        if row.status == "proposed":
            counts[row.kind] = counts.get(row.kind, 0) + 1

    probes = skipped = 0
    stopped = "done"
    for candidate in find_candidates(snapshot, dialect=dialect, allows=scope.allows, known=known):
        evidence = _evidence(candidate)
        if can_probe:
            if stopped == "done" and probes >= settings.max_probes:
                stopped = "max_probes"
            elif stopped == "done" and _now() - started >= settings.time_budget_s:
                stopped = "time_budget"
            if stopped != "done":
                skipped += 1
            else:
                probes += 1
                measured = _probe(adapter, candidate, settings.sample_rows, issues)
                if measured is not None:
                    sampled, matched = measured
                    ratio = matched / sampled if sampled else 0.0  # compared unrounded
                    if ratio < settings.min_overlap:  # an empty sample is not evidence either
                        _withdraw(ctx, _join_proposal(ctx, datasource_id, snapshot, version, candidate, evidence))
                        continue
                    evidence.update(sampled=sampled, matched=matched, overlap=round(ratio, 4))
        file(_join_proposal(ctx, datasource_id, snapshot, version, candidate, evidence))

    for finding in find_roles(snapshot, dialect=dialect, allows=scope.allows, said=said,
                              enum_max_distinct=settings.enum_max_distinct):
        label = table_label(finding.table, snapshot)
        file(Proposal.build(
            kind="column", datasource_id=datasource_id, target=f"{label}.{finding.column.name}",
            change=finding.change, origin="profiler", evidence=finding.evidence, schema_version=version,
            tables=table_names(ctx, datasource_id, [(finding.table.table.schema_name,
                                                     finding.table.table.table_name)])))

    return ProfileReport(datasource_id=datasource_id, schema_version=version, proposals=counts, probes=probes,
                         skipped_by_budget=skipped, seconds=round(_now() - started, 3), stopped_by=stopped,
                         issues=issues)


def _withdraw(ctx: Any, proposal: Proposal) -> None:
    """Supersede the profiler's own open row for a candidate a probe has now measured as a poor match.

    An earlier run may have proposed it with ``overlap: null`` (a bound stopped the probes). The id hashes the
    change, not the evidence, so that row would otherwise stay open for a reviewer to accept.
    """
    row = ctx.state.get_proposal(proposal.id)
    if row is not None and row.status == "proposed" and row.origin == "profiler":
        ctx.state.decide_proposal(row.id, "superseded", by=None)


def _evidence(candidate: JoinCandidate) -> Dict[str, Any]:
    return {"rule": candidate.rule, "types": [candidate.left_column.data_type, candidate.right_column.data_type],
            "category": candidate.category, "overlap": None}


def _probe(adapter: Any, candidate: JoinCandidate, sample_rows: int, issues: List[str]) -> Optional[tuple]:
    """``(sampled, matched)``, or None after recording why the probe failed (never an exception)."""
    left = ColumnRef(table=candidate.left.table, column_name=candidate.left_column.name)
    right = ColumnRef(table=candidate.right.table, column_name=candidate.right_column.name)
    try:
        overlap = adapter.probe_overlap(left, right, sample_rows)
    except RuntimeError as error:  # the adapter's message names the columns and the error class, no values
        issues.append(str(error))
        return None
    except Exception as error:  # a driver's message may quote a value: the class alone
        issues.append(f"probing {candidate.left_column.name} against {candidate.right_column.name} failed "
                      f"({type(error).__name__}).")
        return None
    return overlap.sampled, overlap.matched


def _join_proposal(ctx: Any, datasource_id: str, snapshot: SchemaSnapshot, version: str, candidate: JoinCandidate,
                   evidence: Dict[str, Any]) -> Proposal:
    source = f"{table_label(candidate.left, snapshot)}.{candidate.left_column.name}"
    target = f"{table_label(candidate.right, snapshot)}.{candidate.right_column.name}"
    tables = table_names(ctx, datasource_id, [(candidate.left.table.schema_name, candidate.left.table.table_name),
                                              (candidate.right.table.schema_name, candidate.right.table.table_name)])
    return Proposal.build(
        kind="join", datasource_id=datasource_id, target=f"{source}->{target}",
        change={"from": source, "to": target, "cardinality": candidate.cardinality}, origin="profiler",
        evidence=evidence, schema_version=version, tables=tables)


def _glossary_facts(knowledge: Any, snapshot: SchemaSnapshot, dialect: Optional[str]):
    """The column pairs the glossary already joins, and what it says about each column it names."""
    known: Set[frozenset] = set()
    for join in knowledge.joins:
        resolved = resolve_join(join, snapshot, dialect=dialect)
        if resolved is None:
            continue
        left, left_columns, right, right_columns = resolved
        for a, b in zip(left_columns, right_columns):
            known.add(frozenset({(left.table.full_name, a.lower()), (right.table.full_name, b.lower())}))
    said = {}
    for name, column_knowledge in knowledge.columns.items():
        table, column, _ = resolve_column(name, snapshot)
        if table is not None and column is not None:
            said[(table.table.full_name, column.name.lower())] = column_knowledge
    return frozenset(known), said
