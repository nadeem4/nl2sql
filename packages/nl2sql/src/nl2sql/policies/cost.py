"""The cost rule: the built-in ``policy.cost`` hook (docs/architecture/wave_b.md, section 9; D52 to D54).

``policies.cost`` limits the rows a query is estimated to *read*
(``max_estimated_rows``), and says what to do over the limit (``over_limit``)
and when no estimate can be had (``unknown``): ``allow``, ``confirm`` (a
person approves, as Wave A's ``NeedsApproval``) or ``refuse``
(``COST_LIMIT_EXCEEDED``, never retried). A role's ``cost`` replaces the
top-level fields it sets; a caller with several roles gets each field's most
permissive value, ``null`` (no limit) winning.

The runtime puts the hook first at ``execute.before``, so it sees generated
SQL, verified queries and a correction's check run alike, and no estimate is
asked for unless a limit applies to the caller and the adapter declares
``ESTIMATES_COST``. The estimate is also left for the node that called the
hooks (``take_record``), which puts it on the sub-query's result.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from nl2sql.config.models import CostAction, CostRule, Policies
from nl2sql.events import publish
from nl2sql.hooks import Allow, Deny, HookBinding, HookContext, NeedsApproval
from nl2sql.hooks.runner import COST_HOOK
from nl2sql_sdk.capabilities import DatasourceCapability
from nl2sql_sdk.models import Estimate

logger = logging.getLogger(__name__)

# How permissive an action is: the most permissive of several roles' wins.
_PERMISSIVENESS = {"refuse": 0, "confirm": 1, "allow": 2}


@dataclass(frozen=True)
class CostRecord:
    """What the rule did for one sub-query: the estimate (None when there was none), the limit and the action."""

    estimate: Optional[Estimate]
    limit: int
    decision: CostAction


# The latest record this context's hook made, with the sub-query it is for. The hook and the node that
# called it run in one thread, so the node reads it straight after the call.
_LAST: ContextVar[Optional[Tuple[str, Optional[str], CostRecord]]] = ContextVar("nl2sql_cost_record", default=None)


def take_record(trace_id: str, sub_query_id: Optional[str]) -> Optional[CostRecord]:
    """The record the cost hook just made for this sub-query, or None (no cost rule, no limit). Read once."""
    last = _LAST.get()
    if last is None or last[:2] != (trace_id, sub_query_id):
        return None
    _LAST.set(None)
    return last[2]


def build_cost_guard(policies: Policies, ds_registry: Any) -> Optional["CostGuard"]:
    """The guard for ``policies``, or None when no ``cost`` block (at the top or on a role) asks for one."""
    if policies.cost is None and not any(role.cost is not None for role in policies.roles.values()):
        return None
    return CostGuard(policies, ds_registry)


class CostGuard:
    """The ``policy.cost`` hook. ``ds_registry`` says which adapters declare ``ESTIMATES_COST``."""

    points = frozenset({"execute.before"})

    def __init__(self, policies: Policies, ds_registry: Any) -> None:
        self.policies = policies
        self.ds_registry = ds_registry

    def binding(self) -> HookBinding:
        """The hook as the runner runs it: approved by ``policies.admin_roles``, failing closed."""
        return HookBinding(name=COST_HOOK, hook=self, points=self.points, on_error="closed",
                           approver_roles=tuple(self.policies.admin_roles))

    # ---------------------------------------------------------------- the rule

    def applies(self, user: Any) -> bool:
        """Whether the rule gives ``user`` a limit, so that every query of hers is estimated."""
        return self.rule_for(user)[0].max_estimated_rows is not None

    def rule_for(self, user: Any) -> Tuple[CostRule, str]:
        """The rule that applies to ``user``, and where its limit is set (a config path, for the message)."""
        base = self.policies.cost or CostRule()
        roles = list(getattr(user, "roles", None) or []) or ([self.policies.default_role]
                                                             if self.policies.default_role else [])
        known = [name for name in roles if name in self.policies.roles]
        rules = [(name, self._role_rule(base, self.policies.roles[name].cost)) for name in known] or [(None, base)]
        limits = [rule.max_estimated_rows for _, rule in rules]
        limit = None if None in limits else max(limits)
        rule = CostRule(
            max_estimated_rows=limit,
            over_limit=max((r.over_limit for _, r in rules), key=_PERMISSIVENESS.get),
            unknown=max((r.unknown for _, r in rules), key=_PERMISSIVENESS.get),
        )
        source = "policies.cost"
        for name, role_rule in rules:
            override = self.policies.roles[name].cost if name else None
            if override is not None and "max_estimated_rows" in override.model_fields_set \
                    and role_rule.max_estimated_rows == limit:
                source = f"policies.roles.{name}.cost"
                break
        return rule, source

    @staticmethod
    def _role_rule(base: CostRule, override: Optional[CostRule]) -> CostRule:
        if override is None:
            return base
        return base.model_copy(update={name: getattr(override, name) for name in override.model_fields_set})

    # ---------------------------------------------------------------- the hook

    def __call__(self, ctx: HookContext):
        rule, source = self.rule_for(ctx.user)
        limit = rule.max_estimated_rows
        if limit is None:
            return Allow()  # no limit: nothing to compare, so nothing is estimated
        estimate = self._estimate(ctx)
        rows = estimate.rows if estimate is not None else None
        if rows is None:
            action, reason = rule.unknown, ("The rows this query reads could not be estimated, and this role's "
                                            f"limit of {limit:,} ({source}) needs an estimate.")
        elif rows > limit:
            action = rule.over_limit
            reason = (f"This query is estimated to read {rows:,} rows, over this role's limit of {limit:,} "
                      f"({source}). Ask about less data, for example a shorter period.")
        else:
            action, reason = "allow", ""
        self._record(ctx, estimate, limit, action)
        if action == "allow":
            return Allow()
        if action == "confirm":
            return NeedsApproval(reason=f"Estimated to read {rows:,} rows (limit {limit:,})" if rows is not None
                                 else reason)
        return Deny(reason=reason)

    def _estimate(self, ctx: HookContext) -> Optional[Estimate]:
        """The adapter's estimate for ``ctx.sql``, or None: not declared, no datasource, or it failed."""
        if not ctx.datasource_id or not self.ds_registry.supports(ctx.datasource_id,
                                                                  DatasourceCapability.ESTIMATES_COST):
            return None
        try:
            return self.ds_registry.get_adapter(ctx.datasource_id).estimate(ctx.sql or "", ctx.params or {})
        except Exception as exc:
            # The database's text is operator data: the caller is told only that there was no estimate.
            logger.warning("The estimate for datasource '%s' failed, so the cost rule's 'unknown' applies: %s: %s",
                           ctx.datasource_id, type(exc).__name__, exc)
            return None

    @staticmethod
    def _record(ctx: HookContext, estimate: Optional[Estimate], limit: int, decision: CostAction) -> None:
        sub_query_id = ctx.sub_query.id if ctx.sub_query is not None else None
        _LAST.set((ctx.trace_id, sub_query_id, CostRecord(estimate, limit, decision)))
        publish("cost.estimated", {
            "datasource_id": ctx.datasource_id or "", "sub_query_id": sub_query_id,
            "estimated_rows": estimate.rows if estimate is not None else None,
            "cost": estimate.cost if estimate is not None else None, "limit": limit, "decision": decision,
        })
