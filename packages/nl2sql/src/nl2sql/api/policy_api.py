"""
Policy API for NL2SQL.

Provides public entry points for policy validation and integrity checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

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


class PolicyAPI:
    """
    API for policy validation and integrity checks.
    """

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def validate_policies(self) -> PolicyValidationReport:
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
