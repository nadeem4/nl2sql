"""``engine.knowledge.profile``: read the live snapshot, probe a bounded number of join candidates, file proposals.

docs/architecture/wave_b.md, section 6. The run reads the live build's snapshot (it never reflects on its own)
and only the tables the caller's scope reaches (D60), so its proposals name only tables that caller can see.
Probes run in order of candidate strength, one at a time, until ``settings.profiler.max_probes`` or
``time_budget_s`` is reached; the candidates left are proposed with ``overlap: null`` (D45). Everything goes
through the review queue (D38, D41): nothing here writes a file.

With ``draft=True`` (D44) the ``profiler`` model also drafts the descriptions the glossary and the database leave
blank, under one ``Budget`` for the whole call (``max_cost_usd``). A column's role and its drafted description go
into ONE ``column`` proposal: the queue supersedes an open proposal of the same kind, datasource and target, so
filed apart the second would retire the first.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Set, Tuple

from nl2sql_sdk.protocols import ProfilingAdapterProtocol
from nl2sql_sdk.schema import ColumnRef, SchemaSnapshot
from nl2sql_sdk.stores import Proposal

from nl2sql.knowledge.resolve import resolve_column, resolve_join, resolve_table, table_label
from nl2sql.limits import Budget
from nl2sql.profiler.drafts import ENRICHMENT_LLM_NAME, Drafted, draft_descriptions
from nl2sql.profiler.joins import JoinCandidate, find_candidates
from nl2sql.profiler.models import ProfileReport
from nl2sql.profiler.roles import find_roles
from nl2sql.review.queue import propose, table_names

if TYPE_CHECKING:
    from nl2sql.policies import UserContext


def _now() -> float:
    return time.monotonic()


@dataclass
class Drafter:
    """The model and the shared budget a ``draft=True`` run drafts with; ``problem`` instead when it cannot."""

    llm: Any = None
    budget: Optional[Budget] = None
    problem: Optional[str] = None


def _drafter(ctx: Any, max_cost_usd: Optional[float]) -> Drafter:
    """The ``profiler`` model under a budget, or a ``problem`` saying why there is none.

    Raises:
        UnknownPriceError: the model has no price, so its cost could not be capped.
    """
    from nl2sql.evaluation.run import UnknownPriceError
    from nl2sql.llm.prices import price_for
    from nl2sql.services.callbacks.token_handler import TokenUsageCallback

    try:
        llm = ctx.llm_registry.get_llm(ENRICHMENT_LLM_NAME)
    except ValueError as error:
        return Drafter(problem=f"Drafting needs a model for the '{ENRICHMENT_LLM_NAME}' agent, and there is none: "
                               f"{error}")
    prices = dict(ctx.config.llm.prices)
    model = ctx.llm_registry.get_llm_config(ENRICHMENT_LLM_NAME).get("model")
    if price_for(model if isinstance(model, str) else None, prices) is None:
        raise UnknownPriceError(f"No price for {ENRICHMENT_LLM_NAME}: {model}. Add it to llm.prices (USD per 1M "
                                f"tokens: input, output) before drafting.")
    cap = max_cost_usd if max_cost_usd is not None else ctx.config.settings.profiler.max_cost_usd
    return Drafter(llm=llm, budget=Budget(max_cost_usd=cap, usage=TokenUsageCallback(prices=prices),
                                          setting="settings.profiler"))


def profile(ctx: Any, datasource_ids: Sequence[str], user: "UserContext", *, draft: bool = False,
            max_cost_usd: Optional[float] = None) -> List[ProfileReport]:
    """One report per datasource in ``datasource_ids`` the caller reaches.

    ``draft`` also drafts descriptions, under ``max_cost_usd`` (``settings.profiler.max_cost_usd`` when None)
    across the whole call.

    Raises:
        ValueError: ``max_cost_usd`` is not positive.
        UnknownPriceError: ``draft`` is set and the ``profiler`` model has no price.
    """
    if max_cost_usd is not None and max_cost_usd <= 0:
        raise ValueError("max_cost_usd must be a positive number of dollars.")
    drafter = _drafter(ctx, max_cost_usd) if draft else None
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
        report = _profile_datasource(ctx, datasource_id, scope, snapshot, version, started, drafter)
        ctx.bus.publish("profile.finished", {
            "datasource_id": datasource_id, "proposals": report.proposals, "probes": report.probes,
            "skipped_by_budget": report.skipped_by_budget, "drafted": report.drafted,
            "stopped_by": report.stopped_by, "cost": report.cost, "seconds": report.seconds},
            tenant_id=ctx.tenant_id)
        reports.append(report)
    return reports


def _profile_datasource(ctx: Any, datasource_id: str, scope: Any, snapshot: SchemaSnapshot, version: str,
                        started: float, drafter: Optional[Drafter]) -> ProfileReport:
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
    known, said, said_tables = _glossary_facts(knowledge, snapshot, dialect)

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

    drafted, cost, cost_cap = Drafted(), None, False
    if drafter is not None and drafter.problem:
        issues.append(drafter.problem)
    elif drafter is not None:
        spent = drafter.budget.usage.spent()
        drafted = draft_descriptions(
            drafter.llm, drafter.budget, snapshot, allows=scope.allows,
            has_table_text=lambda key: _has_table_text(snapshot, said_tables, key),
            has_column_text=lambda key, column: _has_column_text(snapshot, said, key, column),
            max_chars=ctx.config.settings.knowledge.item_max_chars, issues=issues)
        cost = round(drafter.budget.usage.spent() - spent, 6)
        stop = drafter.budget.stop
        cost_cap = stop is not None and stop.limit == "max_cost_usd"

    for key, text in drafted.tables.items():
        table = snapshot.contract.tables[key]
        file(Proposal.build(
            kind="table", datasource_id=datasource_id, target=table_label(table, snapshot),
            change={"description": text.description}, origin="draft", evidence={"citations": text.citations},
            schema_version=version,
            tables=table_names(ctx, datasource_id, [(table.table.schema_name, table.table.table_name)])))

    roles = {(f.table.table.full_name, f.column.name): f for f in find_roles(
        snapshot, dialect=dialect, allows=scope.allows, said=said, enum_max_distinct=settings.enum_max_distinct)}
    for key in [*roles, *(k for k in drafted.columns if k not in roles)]:
        found, text = roles.get(key), drafted.columns.get(key)
        table = snapshot.contract.tables[key[0]]
        change = dict(found.change) if found else {}
        evidence = dict(found.evidence) if found else {}
        if text is not None:  # one proposal per column: the role and the description together
            change["description"] = text.description
            if text.synonyms:
                change["synonyms"] = text.synonyms
            evidence["draft"] = {"citations": text.citations}
        file(Proposal.build(
            kind="column", datasource_id=datasource_id, target=f"{table_label(table, snapshot)}.{key[1]}",
            change=change, origin="draft" if text is not None else "profiler", evidence=evidence,
            schema_version=version,
            tables=table_names(ctx, datasource_id, [(table.table.schema_name, table.table.table_name)])))

    return ProfileReport(datasource_id=datasource_id, schema_version=version, proposals=counts, probes=probes,
                         skipped_by_budget=skipped, drafted=drafted.count, cost=cost,
                         seconds=round(_now() - started, 3), stopped_by="cost_cap" if cost_cap else stopped,
                         issues=issues)


def _has_table_text(snapshot: SchemaSnapshot, said_tables: Set[str], key: str) -> bool:
    """Whether the glossary or the database already describes the table."""
    metadata = snapshot.metadata.tables.get(key)
    return key in said_tables or bool(metadata and metadata.description)


def _has_column_text(snapshot: SchemaSnapshot, said: Dict[Tuple[str, str], Any], key: str, column: str) -> bool:
    """Whether the glossary or the database already describes the column."""
    metadata = snapshot.metadata.tables.get(key)
    meta = metadata.columns.get(column) if metadata else None
    knowledge = said.get((key, column.lower()))
    return bool(meta and meta.description) or bool(knowledge is not None and knowledge.description)


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
    """The column pairs the glossary already joins, what it says about each column it names, and the tables it
    describes (their full names)."""
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
    said_tables: Set[str] = set()
    for name, table_knowledge in knowledge.tables.items():
        table, _ = resolve_table(name, snapshot)
        if table is not None and table_knowledge.description:
            said_tables.add(table.table.full_name)
    return frozenset(known), said, said_tables
