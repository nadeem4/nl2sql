"""``engine.policies``: the roles, what a caller may read, and whether the patterns match the datasources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from nl2sql.config import Role
from nl2sql.policies import Scope, UserContext, matching_datasources
from nl2sql.runtime import Runtime


@dataclass
class PolicyValidationEntry:
    role: str
    target: str
    status: str
    details: str


@dataclass
class PolicyValidationReport:
    ok: bool
    entries: List[PolicyValidationEntry] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    available_datasources: Set[str] = field(default_factory=set)


class PoliciesAPI:
    """``engine.policies``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def roles(self) -> Dict[str, Role]:
        """The configured roles by id: each one's description and its allow and deny patterns."""
        return dict(self._ctx.config.policies.roles)

    def scope(self, user_context: UserContext, datasource_id: str) -> Scope:
        """What ``user_context`` may read in ``datasource_id``: the same ``Scope`` a run decides with."""
        return self._ctx.policy_engine.scope(user_context, datasource_id)

    def allows(self, user_context: UserContext, datasource_id: str, table: str,
               schema: Optional[str] = None) -> bool:
        """Whether ``user_context`` may read ``table``; ``schema`` defaults to the datasource's default schema."""
        return self.scope(user_context, datasource_id).allows(schema, table)

    def validate(self) -> PolicyValidationReport:
        """
        Check that every pattern the engine's ``policies`` name matches one of
        its datasources. A pattern for a datasource that is not configured is
        reported here rather than refused at startup, since datasources can be
        added at runtime. The patterns' own shape was checked when the config
        loaded.
        """
        registry = self._ctx.ds_registry
        available = set(registry.list_ids())
        unavailable = registry.unavailable()

        entries: List[PolicyValidationEntry] = []
        for role_id, role in self._ctx.config.policies.roles.items():
            for kind, patterns in (("Allow", role.allow), ("Deny", role.deny)):
                for text in patterns:
                    entries.append(self._entry(role_id, f"{kind}: {text}", text, available, unavailable))

        return PolicyValidationReport(
            ok=all(e.status == "OK" for e in entries),
            entries=entries,
            available_datasources=available,
        )

    @staticmethod
    def _entry(role: str, target: str, text: str, available: Set[str],
               unavailable: Dict[str, str]) -> PolicyValidationEntry:
        failed = matching_datasources(text, unavailable)
        matched = matching_datasources(text, available)
        if failed and not matched:
            reasons = "; ".join(f"{ds}: {unavailable[ds]}" for ds in failed)
            return PolicyValidationEntry(role, target, "UNAVAILABLE", f"Datasource failed to start: {reasons}")
        if not matched:
            return PolicyValidationEntry(role, target, "MISSING", "No configured datasource matches it")
        return PolicyValidationEntry(role, target, "OK", f"Matches {', '.join(matched)}")
