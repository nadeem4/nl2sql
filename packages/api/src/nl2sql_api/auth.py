"""Where the caller's RBAC role comes from.

The engine enforces RBAC; this module only decides which role a request carries.
It is a seam, not an auth provider: put a proxy that authenticates the caller in
front of the API and have it set the role header, or pin one static role.

The settings are the ``api`` section of ``nl2sql.yaml`` (``engine.config.api``).
Sources, first match wins:

1. ``api.role_header``: the name of a header a trusted proxy sets, for example
   ``X-NL2SQL-Role``. Comma-separated values are several roles. Only configure
   it when the proxy overwrites that header on every request, or any client can
   pick its own role.
2. ``api.role``: one static role for every request.
3. ``user_context`` in the request body, only with the dev flag
   ``api.trust_body_role: true``. For local testing: it lets any client choose
   any role.

No role is a 401.

Admin routes (anything that changes datasources, LLMs or the index) also depend
on ``require_admin``: one of the resolved roles must be in
``policies.admin_roles`` (default ``[admin]``), or the request is a 403. With
none of the three role sources configured, admin routes answer loopback clients
only, so a fresh local install still works and a published port does not hand
out admin.
"""

import ipaddress
import logging
from typing import Any, List

from fastapi import Depends, HTTPException, Request, status
from pydantic import ValidationError

from nl2sql import NL2SQL, UserContext
from nl2sql_api.dependencies import get_engine

logger = logging.getLogger(__name__)


def _roles(value: str) -> List[str]:
    return [role.strip() for role in value.split(",") if role.strip()]


def warn_if_body_role_trusted(api: Any) -> None:
    """Log a warning when the dev flag lets clients choose their own role."""
    if api.trust_body_role:
        logger.warning(
            "api.trust_body_role is on: the RBAC role is taken from the request body, so any "
            "client can choose any role. Use it for local testing only."
        )


async def _body_user_context(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        return None
    return body.get("user_context") if isinstance(body, dict) else None


async def get_user_context(request: Request, engine: NL2SQL = Depends(get_engine)) -> UserContext:
    """Return the caller's ``UserContext``, or raise 401 when no role is configured."""
    api = engine.config.api
    header = (api.role_header or "").strip()
    if header:
        roles = _roles(request.headers.get(header, ""))
        if roles:
            return UserContext(roles=roles)

    static_role = (api.role or "").strip()
    if static_role:
        return UserContext(roles=_roles(static_role))

    if api.trust_body_role:
        payload = await _body_user_context(request)
        if isinstance(payload, dict):
            try:
                user_context = UserContext(**payload)
            except ValidationError:
                user_context = None
            if user_context and user_context.roles:
                return user_context

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            "No role for this request. Configure a trusted proxy header "
            "(api.role_header) or a static role (api.role) in nl2sql.yaml. The body's "
            "user_context is ignored unless api.trust_body_role is true (local testing only)."
        ),
    )


def _role_source_configured(api: Any) -> bool:
    return bool((api.role_header or "").strip() or (api.role or "").strip() or api.trust_body_role)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def require_admin(request: Request, engine: NL2SQL = Depends(get_engine)) -> UserContext:
    """Let the request through only if it carries an admin role; 401 with no role, 403 without admin.

    With no role source configured, only a loopback client is let through.
    """
    admin_roles = list(engine.config.policies.admin_roles)
    if not _role_source_configured(engine.config.api):
        host = request.client.host if request.client else ""
        if _is_loopback(host):
            return UserContext(roles=admin_roles)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Admin routes answer only loopback clients until a role source is configured. "
                "Set a trusted proxy header (api.role_header) or a static role (api.role) in "
                f"nl2sql.yaml, and list the admin roles in policies.admin_roles (now: {', '.join(admin_roles)})."
            ),
        )

    user_context = await get_user_context(request, engine)
    if not set(user_context.roles) & set(admin_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This route needs an admin role: one of policies.admin_roles ({', '.join(admin_roles)}).",
        )
    return user_context


def is_fresh_local_install(request: Request, engine: NL2SQL) -> bool:
    """Whether this is the fresh local install: no role source is configured and the client is on loopback.

    That is the one case ``require_admin`` lets through without a role, and the one case where a secret
    typed into a Connect form may be written to the ``.env`` beside ``nl2sql.yaml`` (D122). Anywhere else a
    literal secret is refused and the form must send a ``${env:...}`` reference.
    """
    if _role_source_configured(engine.config.api):
        return False
    return _is_loopback(request.client.host if request.client else "")


ADMIN_RESPONSES = {
    401: {"description": "No role for this request (see the auth settings)."},
    403: {"description": "The request has no role listed in policies.admin_roles, or no role source "
                         "is configured and the client is not on loopback."},
}
