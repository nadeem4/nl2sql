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

from typing import TYPE_CHECKING, Any, Iterable, List, Optional, Tuple

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


def propose(ctx: Any, proposal: Proposal) -> Proposal:
    """File ``proposal`` in the queue, by its identity and the supersede rule; return the row as stored.

    Publishes ``proposal.created`` when the row is new, or was superseded and
    is proposed again.
    """
    state = ctx.state
    existing = state.get_proposal(proposal.id)
    if existing is not None and existing.status in ("accepted", "rejected"):
        return existing
    if existing is not None and existing.status == "proposed":
        row = existing.model_copy(update={"evidence": proposal.evidence})
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
