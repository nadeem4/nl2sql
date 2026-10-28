"""``engine.api_keys``: bearer keys for MCP over HTTP (docs/architecture/wave_c.md, section 9; D127).

A stub with its final signatures; M1 fills it. Managing keys needs an admin ``by`` with no default (D130);
``verify`` takes the token alone and returns the key's ``UserContext``.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from nl2sql.api.types import ApiKey
from nl2sql.common.planned import arrives_with
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime


class ApiKeysAPI:
    """``engine.api_keys``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def create(self, *, user_id: str, roles: List[str], by: UserContext, tenant_id: Optional[str] = None,
               label: str = "") -> Tuple[ApiKey, str]:
        """A new key and its token, which is returned once; only the token's hash is stored."""
        raise arrives_with("engine.api_keys.create", "M1")

    def list(self, *, by: UserContext) -> List[ApiKey]:
        """Every key, never its secret."""
        raise arrives_with("engine.api_keys.list", "M1")

    def revoke(self, key_id: str, *, by: UserContext) -> None:
        """Revokes a key; it stops verifying at once."""
        raise arrives_with("engine.api_keys.revoke", "M1")

    def verify(self, token: str) -> Optional[UserContext]:
        """The key's ``UserContext``, or None for a malformed, unknown or revoked token."""
        raise arrives_with("engine.api_keys.verify", "M1")
