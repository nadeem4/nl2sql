from .models import RolePolicy
from .models import UserContext
from typing import List, Dict

class RBAC:
    def __init__(self, policies: Dict[str, RolePolicy]):
        self.policies = policies

    def is_allowed(self, user_ctx: UserContext, datasource_id: str, table: str) -> bool:
        policy =  [self.policies.get(role) for role in user_ctx.roles]
        if not policy:
            return False
        return any(p.is_allowed(datasource_id, table) for p in policy)
    
    def get_allowed_tables(self, user_ctx: UserContext) -> List[str]:
        policy = [self.policies.get(role) for role in user_ctx.roles]
        if not policy:
            return []
        return list(set().union(*[p.allowed_tables for p in policy]))
    
    def get_allowed_datasources(self, user_ctx: UserContext) -> List[str]:
        policy = [self.policies.get(role) for role in user_ctx.roles]
        if not policy:
            return []
        return list(set().union(*[p.allowed_datasources for p in policy]))