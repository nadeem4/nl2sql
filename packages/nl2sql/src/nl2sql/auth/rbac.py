from .models import RolePolicy
from .models import UserContext
from typing import Iterable, List, Dict


def table_allowed(allowed_tables: Iterable[str], datasource_id: str, table: str) -> bool:
    """Whether a table is readable under a list of ``allowed_tables`` entries.

    An entry is ``*`` (every table), ``<datasource>.*`` (every table in one
    datasource) or an exact ``<datasource>.<table>``. The logical validator and
    the schema retriever both decide with this function, so the table a plan is
    refused for is exactly the table whose data the planner was never shown.
    """
    allowed = set(allowed_tables)
    return "*" in allowed or f"{datasource_id}.*" in allowed or f"{datasource_id}.{table}" in allowed


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

    def is_allowed(self, user_ctx: UserContext, datasource_id: str, table: str) -> bool:
        return table_allowed(self.get_allowed_tables(user_ctx), datasource_id, table)

    def get_allowed_tables(self, user_ctx: UserContext) -> List[str]:
        return list(set().union(*[p.allowed_tables for p in self._policies_for(user_ctx)]))

    def get_allowed_datasources(self, user_ctx: UserContext) -> List[str]:
        return list(set().union(*[p.allowed_datasources for p in self._policies_for(user_ctx)]))
