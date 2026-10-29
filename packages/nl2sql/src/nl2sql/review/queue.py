"""The review queue: filing a proposal, who may see one, and who may decide (docs/architecture/wave_b.md, section 5).

``propose`` is how every track files a finding (D41):

- **Identity.** A proposal's id hashes its kind, datasource, target and
  change. The same change filed again updates the open row's evidence; a
  rejected or accepted change is never proposed again.
- **Supersede.** A new change for a kind, datasource and target that has an
  open (``proposed``) row marks that row ``superseded``, so the queue holds one
  open item per target. Decided rows are never superseded.

A caller sees a proposal only when their scope reaches its datasource and
every table in its ``tables`` (D60); deciding needs ``by`` with an admin role.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from nl2sql_sdk.stores import Proposal

from nl2sql.review.models import ReviewError, ReviewErrorCode

if TYPE_CHECKING:
    from nl2sql.policies import UserContext


def caller(user_context: Any, name: str = "user_context") -> "UserContext":
    """The caller, required: there is no default view and no default decider (D60)."""
    from nl2sql.policies import UserContext

    if not isinstance(user_context, UserContext):
        raise TypeError(f"{name} must be a UserContext: the caller is always named. "
                        "Deciding needs a caller with an admin role (policies.admin_roles).")
    return user_context


def who(by: "UserContext") -> Optional[str]:
    """What a decision records as its decider: the user id, else the roles."""
    return by.user_id or (",".join(by.roles) or None)


def require_admin(ctx: Any, by: Any) -> "UserContext":
    """``by``, when it holds one of ``policies.admin_roles``; under ``allow_all`` every caller may decide.

    Raises:
        TypeError: ``by`` is not a ``UserContext``.
        ReviewError: ``NOT_AUTHORIZED``.
    """
    by = caller(by, "by")
    policies = ctx.config.policies
    if not (policies.allow_all or set(by.roles) & set(policies.admin_roles)):
        raise ReviewError(ReviewErrorCode.NOT_AUTHORIZED,
                          f"Deciding a proposal needs one of the admin roles "
                          f"({', '.join(policies.admin_roles) or 'none defined'}).",
                          fix="Accept or reject as a caller with an admin role (policies.admin_roles).")
    return by


def split_table(name: str) -> Tuple[str, Optional[str], str]:
    """``datasource.schema.table`` (or ``datasource.table``) as its three parts; the schema may be None."""
    datasource, _, rest = name.partition(".")
    schema, _, table = rest.rpartition(".")
    return datasource, schema or None, table


def visible(ctx: Any, user: "UserContext", proposal: Proposal) -> bool:
    """Whether ``user``'s scope reaches the proposal's datasource and every table it names (D60)."""
    if proposal.datasource_id not in ctx.ds_registry.list_ids():
        return False
    scopes = {}

    def scope(datasource_id: str):
        if datasource_id not in scopes:
            scopes[datasource_id] = ctx.policy_engine.scope(user, datasource_id)
        return scopes[datasource_id]

    if not scope(proposal.datasource_id).reaches_datasource():
        return False
    for name in proposal.tables:
        datasource_id, schema, table = split_table(name)
        if datasource_id != proposal.datasource_id or not scope(datasource_id).allows(schema, table):
            return False
    return True


def table_names(ctx: Any, datasource_id: str, tables: Iterable[Tuple[str, str]]) -> List[str]:
    """``(schema or "", table)`` pairs as ``datasource.schema.table``, the schema the adapter's default when blank."""
    from nl2sql.policies.scope import default_schema

    fallback = default_schema(ctx.ds_registry, datasource_id)
    return sorted({f"{datasource_id}.{schema or fallback}.{table}" for schema, table in tables})


def _table_of(name: str) -> str:
    """The table part of a column name: ``Invoice.Total`` is ``Invoice``, ``sales.Invoice.Total`` is ``sales.Invoice``."""
    return name.rpartition(".")[0]


def _sides(value: Any) -> List[str]:
    return [value] if isinstance(value, str) else [v for v in value or [] if isinstance(v, str)]


def named_tables(ctx: Any, proposal: Proposal) -> List[str]:
    """Every table the proposal's target and change name, as ``datasource.schema.table``.

    A table is ``table`` or ``schema.table``, a column ``table.column`` or
    ``schema.table.column``; a verified query's tables are read from its SQL
    in the datasource's dialect.
    """
    change, names = proposal.change, []
    if proposal.kind == "table":
        names = [proposal.target]
    elif proposal.kind == "column":
        names = [_table_of(proposal.target)]
    elif proposal.kind == "join":
        names = [_table_of(n) for n in _sides(change.get("from")) + _sides(change.get("to"))]
    elif proposal.kind == "term":
        names = _sides(change.get("tables")) + [_table_of(n) for n in _sides(change.get("columns"))]
    elif proposal.kind == "verified_query" and isinstance(change.get("sql"), str):
        from nl2sql.config.curated import inspect_verified_sql

        try:
            dialect = ctx.ds_registry.get_dialect(proposal.datasource_id)
            pairs = inspect_verified_sql(change["sql"], dialect).tables
        except Exception:  # not one read-only query, or no such datasource: nothing to read from it
            pairs = []
        return table_names(ctx, proposal.datasource_id, pairs)
    pairs = []
    for name in (n.strip() for n in names if n and n.strip()):
        schema, _, table = name.rpartition(".")
        pairs.append((schema, table))
    return table_names(ctx, proposal.datasource_id, pairs)


def propose(ctx: Any, proposal: Proposal) -> Proposal:
    """File ``proposal`` in the queue, by its identity and the supersede rule; return the row as stored.

    ``tables`` is completed with every table the target and change name
    (``named_tables``), so a caller who cannot read one of them never sees it
    (D60). Publishes ``proposal.created`` when the row is new, or was
    superseded and is proposed again.
    """
    given = {t.lower() for t in proposal.tables}
    missing = [t for t in named_tables(ctx, proposal) if t.lower() not in given]
    if missing:
        proposal = proposal.model_copy(update={"tables": sorted([*proposal.tables, *missing])})
    state = ctx.state
    existing = state.get_proposal(proposal.id)
    if existing is not None and existing.status in ("accepted", "rejected"):
        return existing
    if existing is not None and existing.status == "proposed":
        # The new evidence may name other tables (a correction's original SQL): the row keeps both sets.
        row = existing.model_copy(update={"evidence": proposal.evidence,
                                          "tables": sorted({*existing.tables, *proposal.tables})})
        state.put_proposal(row)
        return row
    state.put_proposal(proposal)
    for other in state.list_proposals(kind=proposal.kind, status="proposed", datasource_id=proposal.datasource_id):
        if other.id != proposal.id and other.target.casefold() == proposal.target.casefold():
            state.decide_proposal(other.id, "superseded", by=None)
    ctx.bus.publish("proposal.created", {"id": proposal.id, "kind": proposal.kind,
                                         "datasource_id": proposal.datasource_id, "origin": proposal.origin},
                    tenant_id=ctx.tenant_id)
    return proposal


def current_value(ctx: Any, user: "UserContext", proposal: Proposal) -> Optional[Dict[str, Any]]:
    """What ``proposal`` would replace, as ``user`` may see it; None when there is none or it is hidden.

    For a glossary kind, the item in the merged knowledge as ``user`` sees it
    (K1's ``engine.knowledge.show``, which hides an item on or mentioning a
    denied table). For a verified query, the curated query with the same id,
    only when ``user``'s scope reaches every table its SQL reads.
    """
    target = proposal.target.strip().lower()
    if proposal.kind == "verified_query":
        curated = ctx.curated
        section = curated.datasources.get(proposal.datasource_id) if curated else None
        found = next((q for q in (section.verified_queries if section else []) if q.id == proposal.change.get("id")),
                     None)
        if found is None:
            return None
        value = found.model_dump(mode="json", exclude_defaults=True)
        probe = Proposal.build(kind="verified_query", datasource_id=proposal.datasource_id, target=proposal.target,
                               change=value, origin=proposal.origin)
        probe = probe.model_copy(update={"tables": named_tables(ctx, probe)})
        return value if probe.tables and visible(ctx, user, probe) else None
    from nl2sql.api.knowledge_api import KnowledgeAPI

    views = KnowledgeAPI(ctx).show(proposal.datasource_id, user_context=user)
    if not views:
        return None
    knowledge = views[0].knowledge
    if proposal.kind in ("table", "column"):
        items = knowledge.tables if proposal.kind == "table" else knowledge.columns
        item = next((v for k, v in items.items() if k.strip().lower() == target), None)
    elif proposal.kind == "term":
        item = next((t for t in knowledge.terms if t.term.strip().lower() == target), None)
    elif proposal.kind == "example":
        found = next((e for e in knowledge.examples if e.strip().lower() == target), None)
        return {"question": found} if found is not None else None
    else:
        item = next((j for j in knowledge.joins
                     if "->".join(",".join(_sides(s)) for s in (j.from_, j.to)).lower() == target.replace(" ", "")),
                    None)
    return item.model_dump(mode="json", by_alias=True, exclude_defaults=True) if item is not None else None
