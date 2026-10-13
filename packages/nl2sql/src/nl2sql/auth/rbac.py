from .models import RolePolicy
from .models import UserContext
from typing import Any, Iterable, List, Dict, Optional

# The default schema when the adapter cannot report one: the schema SQLite and
# DuckDB put every table in.
FALLBACK_DEFAULT_SCHEMA = "main"


def table_allowed(
    allowed_tables: Iterable[str],
    datasource_id: str,
    table: str,
    schema: Optional[str] = None,
    default_schema: Optional[str] = None,
) -> bool:
    """Whether ``schema.table`` in a datasource is readable under ``allowed_tables``.

    An entry is ``*`` (every table), ``<datasource>.*`` (every table in one
    datasource), ``<datasource>.<schema>.*`` (every table in one schema),
    ``<datasource>.<schema>.<table>``, or the legacy ``<datasource>.<table>``,
    which means that table in the datasource's default schema only. A table
    with no schema is in the default schema, since that is where the database
    resolves it. When the adapter cannot report a default, ``main`` (SQLite's
    and DuckDB's only schema) stands in for it.

    The logical validator, the schema retriever, the executor's value check and
    the curated matcher all decide with this function, so the table a plan is
    refused for is exactly the table whose data the planner was never shown.
    """
    allowed = set(allowed_tables)
    default_schema = default_schema or FALLBACK_DEFAULT_SCHEMA
    if "*" in allowed or f"{datasource_id}.*" in allowed:
        return True
    effective = schema or default_schema
    if effective and (
        f"{datasource_id}.{effective}.*" in allowed or f"{datasource_id}.{effective}.{table}" in allowed
    ):
        return True
    in_default_schema = not schema or schema == default_schema
    return in_default_schema and f"{datasource_id}.{table}" in allowed


def default_schema(ds_registry: Any, datasource_id: str) -> Optional[str]:
    """The datasource's default schema as its adapter reports it, else ``None``.

    An adapter reports it as a ``default_schema`` attribute; one that does not,
    or a datasource the registry does not know, has none, and ``table_allowed``
    falls back to ``main``.
    """
    try:
        value = getattr(ds_registry.get_adapter(datasource_id), "default_schema", None)
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


class RBAC:
    def __init__(self, policies: Dict[str, RolePolicy]):
        self.policies = policies

    def _policies_for(self, user_ctx: UserContext) -> List[RolePolicy]:
        """The policies of the caller's known roles.

        A role with no policy grants nothing, and no roles at all grants
        nothing: both end in a normal denial rather than an exception.
        """
        roles = user_ctx.roles if user_ctx else []
        return [self.policies[role] for role in roles if role in self.policies]

    def is_allowed(
        self,
        user_ctx: UserContext,
        datasource_id: str,
        table: str,
        schema: Optional[str] = None,
        default_schema: Optional[str] = None,
    ) -> bool:
        return table_allowed(self.get_allowed_tables(user_ctx), datasource_id, table, schema, default_schema)

    def get_allowed_tables(self, user_ctx: UserContext) -> List[str]:
        return list(set().union(*[p.allowed_tables for p in self._policies_for(user_ctx)]))

    def get_allowed_datasources(self, user_ctx: UserContext) -> List[str]:
        return list(set().union(*[p.allowed_datasources for p in self._policies_for(user_ctx)]))
