"""
Policy API for NL2SQL.

Provides public entry points for policy validation and integrity checks.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import List, Optional, Set

from nl2sql.common.settings import settings
from nl2sql.configs import ConfigManager
from nl2sql.context import NL2SQLContext
from nl2sql.datasources import DatasourceRegistry
from nl2sql.secrets import secret_manager


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

    def __init__(self, ctx: Optional[NL2SQLContext] = None):
        self._ctx = ctx

    def validate_policies(
        self,
        policies_path: Optional[pathlib.Path] = None,
        datasources_path: Optional[pathlib.Path] = None,
        secrets_path: Optional[pathlib.Path] = None,
    ) -> PolicyValidationReport:
        """
        Validate policy syntax and integrity against defined datasources.
        """
        cm = self._ctx.config_manager if self._ctx else ConfigManager()

        policies_path = pathlib.Path(policies_path) if policies_path else pathlib.Path(settings.policies_config_path)
        datasources_path = pathlib.Path(datasources_path) if datasources_path else pathlib.Path(settings.datasource_config_path)
        secrets_path = pathlib.Path(secrets_path) if secrets_path else pathlib.Path(settings.secrets_config_path)

        try:
            policy_cfg = cm.load_policies(policies_path)
        except Exception as e:
            return PolicyValidationReport(ok=False, errors=[str(e)])

        try:
            secret_configs = cm.load_secrets(secrets_path)
            if secret_configs:
                secret_manager.configure(secret_configs)

            ds_configs = cm.load_datasources(datasources_path)
            registry = DatasourceRegistry(secret_manager)
            registry.register_datasources(ds_configs)
            available_ds = set(registry.list_ids())
        except Exception as e:
            return PolicyValidationReport(ok=False, errors=[str(e)])

        entries: List[PolicyValidationEntry] = []
        has_errors = False

        for role_id, role_def in policy_cfg.roles.items():
            for ds in role_def.allowed_datasources:
                if ds == "*":
                    entries.append(
                        PolicyValidationEntry(role=role_id, target="Datasource: *", status="OK", details="Global Access")
                    )
                    continue
                if ds not in available_ds:
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
