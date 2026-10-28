"""``engine.api_keys``: bearer keys for MCP over HTTP (docs/architecture/wave_c.md, section 9; D127).

Managing keys needs an admin ``by`` with no default (D130); ``verify`` takes the token alone and returns the key's
``UserContext``. The token is ``nl2sql_<id>_<secret>`` (``nl2sql.policies.keys``); only the secret's hash is stored.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from nl2sql.api.types import ApiKey
from nl2sql.policies import UserContext, keys
from nl2sql.runtime import Runtime

_ID_ATTEMPTS = 8  # a clash of two random 32-bit ids is rare; this many in a row means the generator is broken


@dataclass(frozen=True)
class KeyCheck:
    """What ``engine.api_keys.check`` found: the key's ``user`` when the token is good, else the ``reason``
    ("malformed", "unknown" or "revoked"). ``key_id`` is set only when the secret matched, so an id alone
    never says anything about a key."""

    user: Optional[UserContext] = None
    reason: Optional[str] = None
    key_id: Optional[str] = None


class ApiKeysAPI:
    """``engine.api_keys``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def create(self, *, user_id: str, roles: List[str], by: UserContext, tenant_id: Optional[str] = None,
               label: str = "") -> Tuple[ApiKey, str]:
        """A new key and its token. The token is returned once; only its hash is stored.

        The key carries ``user_id``, ``tenant_id`` and ``roles``: every request with its token runs as them.
        Publishes ``api_key.created`` (its id, never the token).

        Raises:
            TypeError: ``by`` is not a ``UserContext``.
            PermissionError: ``by`` holds none of ``policies.admin_roles``.
            ValueError: ``user_id`` is empty, or a role is not one of ``policies.roles``.
            RuntimeError: no unused key id was found (not expected).
        """
        by = self._admin(by)
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("A key needs a user_id: the user its runs are recorded under.")
        self._check_roles(roles)
        created_at = datetime.now(timezone.utc)
        for _ in range(_ID_ATTEMPTS):
            key_id = keys.new_id()
            token, hashed = keys.new_token(key_id)
            row = {"id": key_id, "hash": hashed, "label": label, "user_id": user_id, "tenant_id": tenant_id,
                   "roles": list(roles), "created_at": created_at.isoformat(), "created_by": _who(by),
                   "revoked_at": None}
            try:
                self._ctx.state.add_api_key(row)
            except ValueError:  # the id is taken: another key keeps it, this one picks again
                continue
            self._publish("api_key.created", row, by)
            return _key(row), token
        raise RuntimeError(f"Could not find an unused API key id in {_ID_ATTEMPTS} tries.")

    def list(self, *, by: UserContext) -> List[ApiKey]:
        """Every key, newest first, revoked ones included; never a secret or a hash.

        Raises:
            TypeError: ``by`` is not a ``UserContext``.
            PermissionError: ``by`` holds none of ``policies.admin_roles``.
        """
        self._admin(by)
        rows = sorted(self._ctx.state.list_api_keys(), key=lambda row: row["created_at"], reverse=True)
        return [_key(row) for row in rows]

    def revoke(self, key_id: str, *, by: UserContext) -> None:
        """Revoke a key; it stops verifying at once. Revoking a revoked key changes nothing.

        Publishes ``api_key.revoked``.

        Raises:
            TypeError: ``by`` is not a ``UserContext``.
            PermissionError: ``by`` holds none of ``policies.admin_roles``.
            ValueError: there is no such key.
        """
        by = self._admin(by)
        row = self._ctx.state.get_api_key(key_id)
        if row is None:
            raise ValueError(f"No API key with id '{key_id}'.")
        if row.get("revoked_at"):
            return
        revoked_at = datetime.now(timezone.utc).isoformat()
        if not self._ctx.state.revoke_api_key(key_id, revoked_at):
            raise ValueError(f"No API key with id '{key_id}'.")
        self._publish("api_key.revoked", row, by)

    def verify(self, token: str) -> Optional[UserContext]:
        """The key's ``UserContext`` (its user, tenant and roles), or None for a malformed, unknown or revoked
        token. The secret's hash is compared in constant time. Takes no caller: it is how a caller is found."""
        return self.check(token).user

    def check(self, token: str) -> KeyCheck:
        """As ``verify``, saying why a token was refused. The secret is compared before a key is called revoked,
        so a right id with a wrong secret is "unknown": an id alone cannot probe a key's state."""
        parsed = keys.parse(token)
        if parsed is None:
            return KeyCheck(reason="malformed")
        key_id, secret = parsed
        row = self._ctx.state.get_api_key(key_id)
        if row is None or not keys.matches(secret, row["hash"]):
            return KeyCheck(reason="unknown")
        if row.get("revoked_at"):
            return KeyCheck(reason="revoked", key_id=key_id)
        user = UserContext(user_id=row["user_id"], tenant_id=row.get("tenant_id"), roles=list(row["roles"]))
        return KeyCheck(user=user, key_id=key_id)

    def record_denied(self, reason: str, key_id: Optional[str] = None) -> None:
        """Publishes ``mcp.denied`` for a request the MCP server refused (``reason``: "missing", "malformed",
        "unknown" or "revoked"). Never takes the token.

        Raises:
            ValueError: ``reason`` is not one of the four.
        """
        self._ctx.bus.publish("mcp.denied", {"reason": reason, "key_id": key_id}, tenant_id=self._ctx.tenant_id)

    # ------------------------------------------------------------ helpers

    def _admin(self, by: Any) -> UserContext:
        if not isinstance(by, UserContext):
            raise TypeError("by must be a UserContext: managing API keys needs a caller with an admin role "
                            "(policies.admin_roles).")
        policies = self._ctx.config.policies
        if not (policies.allow_all or set(by.roles) & set(policies.admin_roles)):
            raise PermissionError(f"Managing API keys needs one of the admin roles "
                                  f"({', '.join(policies.admin_roles) or 'none defined'}).")
        return by

    def _check_roles(self, roles: List[str]) -> None:
        # No allow_all exemption: keys outlive the policy, so a role name no policy defines is never stored.
        policies = self._ctx.config.policies
        unknown = [role for role in roles if role not in policies.roles]
        if unknown:
            defined = ", ".join(policies.roles) or "none"
            raise ValueError(f"Unknown role(s) {', '.join(unknown)}: a key's roles must be configured in "
                             f"policies.roles ({defined}).")

    def _publish(self, name: str, row: Dict[str, Any], by: UserContext) -> None:
        self._ctx.bus.publish(name, {"key_id": row["id"], "user_id": row["user_id"], "tenant_id": row["tenant_id"],
                                     "roles": list(row["roles"]), "by": _who(by)},
                              tenant_id=self._ctx.tenant_id)


def _who(by: UserContext) -> Optional[str]:
    """What a key records as its creator: the user id, else the roles."""
    return by.user_id or (",".join(by.roles) or None)


def _key(row: Dict[str, Any]) -> ApiKey:
    """The listed key: the stored row without its hash."""
    return ApiKey(id=row["id"], label=row["label"], user_id=row["user_id"], tenant_id=row["tenant_id"],
                  roles=list(row["roles"]), created_at=datetime.fromisoformat(row["created_at"]),
                  created_by=row["created_by"],
                  revoked_at=datetime.fromisoformat(row["revoked_at"]) if row.get("revoked_at") else None)
