"""``engine.policies``: the roles, what a caller may read, and whether the rules match the datasources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from nl2sql.auth.models import UserContext
from nl2sql.auth.rbac import default_schema
from nl2sql.config import Role
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
        """The configured roles by id: each one's description and what it may read."""
        return dict(self._ctx.config.policies.roles)

    def allows(self, user_context: UserContext, datasource_id: str, table: str,
               schema: Optional[str] = None) -> bool:
        """Whether ``user_context`` may read ``table``; ``schema`` defaults to the datasource's default schema."""
        default = default_schema(self._ctx.ds_registry, datasource_id)
        return self._ctx.rbac.is_allowed(user_context, datasource_id, table, schema, default)

    def allowed(self, user_context: UserContext) -> Dict[str, List[str]]:
        """What ``user_context`` may read: ``{"datasources": [...], "tables": [...]}``, as the rules say it."""
        rbac = self._ctx.rbac
        return {"datasources": rbac.get_allowed_datasources(user_context),
                "tables": rbac.get_allowed_tables(user_context)}

    def validate(self) -> PolicyValidationReport:
        """
        Check that every datasource and table rule the engine's ``policies``
        name is one of its datasources. The policies' own shape was checked
        when the config loaded.
        """
        policy_cfg = self._ctx.config.policies
        registry = self._ctx.ds_registry
        available_ds = set(registry.list_ids())
        unavailable_ds = registry.unavailable()

        entries: List[PolicyValidationEntry] = []
        has_errors = False

        for role_id, role_def in policy_cfg.roles.items():
            for ds in role_def.allowed_datasources:
                if ds == "*":
                    entries.append(
                        PolicyValidationEntry(role=role_id, target="Datasource: *", status="OK", details="Global Access")
                    )
                    continue
                if ds in unavailable_ds:
                    entries.append(
                        PolicyValidationEntry(
                            role=role_id,
                            target=f"Datasource: {ds}",
                            status="UNAVAILABLE",
                            details=f"Datasource failed to start: {unavailable_ds[ds]}",
                        )
                    )
                    has_errors = True
                elif ds not in available_ds:
                    entries.append(
                        PolicyValidationEntry(
                            role=role_id,
                            target=f"Datasource: {ds}",
                            status="MISSING",
                            details="Datasource not defined in config",
                        )
                    )
                    has_errors = True
                else:
                    entries.append(
                        PolicyValidationEntry(role=role_id, target=f"Datasource: {ds}", status="OK", details="Verified")
                    )

            for rule in role_def.allowed_tables:
                if rule == "*":
                    entries.append(
                        PolicyValidationEntry(role=role_id, target="Table: *", status="OK", details="Global Access")
                    )
                    continue

                parts = rule.split(".")
                if len(parts) >= 2:
                    ds_part = parts[0]
                    if ds_part not in available_ds and ds_part != "*":
                        entries.append(
                            PolicyValidationEntry(
                                role=role_id,
                                target=f"Table Rule: {rule}",
                                status="INVALID_DS",
                                details=f"Datasource '{ds_part}' unknown",
                            )
                        )
                        has_errors = True
                    else:
                        entries.append(
                            PolicyValidationEntry(
                                role=role_id,
                                target=f"Table Rule: {rule}",
                                status="OK",
                                details="DS Verified",
                            )
                        )

        return PolicyValidationReport(
            ok=not has_errors,
            entries=entries,
            available_datasources=available_ds,
        )
