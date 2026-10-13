"""
Auth API for NL2SQL

Provides functionality for authentication and role-based access control.
"""

from __future__ import annotations

from typing import List, Optional

from nl2sql.context import NL2SQLContext
from nl2sql.auth.models import UserContext
from nl2sql.auth.rbac import RBAC, default_schema


class AuthAPI:
    """
    API for authentication and role-based access control.
    """
    
    def __init__(self, ctx: NL2SQLContext):
        self._ctx = ctx
        self._rbac: RBAC = ctx.rbac
    
    def check_permissions(
        self,
        user_context: UserContext,
        datasource_id: str,
        table: str,
        schema: Optional[str] = None,
    ) -> bool:
        """
        Check if a user has permission to access a specific resource.
        
        Args:
            user_context: User context with roles
            datasource_id: ID of the datasource
            table: Name of the table
            schema: Schema of the table; ``None`` means the datasource's default schema
            
        Returns:
            True if user has permission, False otherwise
        """
        default = default_schema(getattr(self._ctx, "ds_registry", None), datasource_id)
        return self._rbac.is_allowed(user_context, datasource_id, table, schema, default)
    
    def get_allowed_resources(
        self,
        user_context: UserContext
    ) -> dict:
        """
        Get resources a user has access to.
        
        Args:
            user_context: User context with roles
            
        Returns:
            Dictionary with allowed datasources and tables
        """
        return {
            "datasources": self._rbac.get_allowed_datasources(user_context),
            "tables": self._rbac.get_allowed_tables(user_context)
        }