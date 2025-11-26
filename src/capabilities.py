from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class EngineCapabilities:
    dialect: str
    limit_syntax: str  # e.g., "limit", "top", "fetch"
    supports_ilike: bool
    identifier_quote: str  # e.g., `"`, `""`, `` ` ``
    supports_datetime_funcs: bool
    supports_dry_run: bool


DEFAULT_CAPABILITIES: Dict[str, EngineCapabilities] = {
    "sqlite": EngineCapabilities(
        dialect="sqlite",
        limit_syntax="limit",
        supports_ilike=False,
        identifier_quote='"',
        supports_datetime_funcs=True,
        supports_dry_run=False,
    ),
    "postgres": EngineCapabilities(
        dialect="postgres",
        limit_syntax="limit",
        supports_ilike=True,
        identifier_quote='"',
        supports_datetime_funcs=True,
        supports_dry_run=True,
    ),
    "mysql": EngineCapabilities(
        dialect="mysql",
        limit_syntax="limit",
        supports_ilike=False,
        identifier_quote="`",
        supports_datetime_funcs=True,
        supports_dry_run=False,
    ),
    "sqlserver": EngineCapabilities(
        dialect="sqlserver",
        limit_syntax="top_fetch",
        supports_ilike=False,
        identifier_quote='"',
        supports_datetime_funcs=True,
        supports_dry_run=False,
    ),
}


def get_capabilities(engine: str) -> EngineCapabilities:
    key = engine.lower()
    if key in {"postgresql"}:
        key = "postgres"
    if key in {"mariadb"}:
        key = "mysql"
    if key in {"sql_server", "mssql", "azure_sql"}:
        key = "sqlserver"
    try:
        return DEFAULT_CAPABILITIES[key]
    except KeyError as exc:
        raise KeyError(f"Capabilities not defined for engine: {engine}") from exc
