"""Where the caller's RBAC role comes from.

The engine enforces RBAC; this module only decides which role a request carries.
It is a seam, not an auth provider: put a proxy that authenticates the caller in
front of the API and have it set the role header, or pin one static role.

Sources, first match wins:

1. ``NL2SQL_API_ROLE_HEADER``: the name of a header a trusted proxy sets, for
   example ``X-NL2SQL-Role``. Comma-separated values are several roles. Only
   configure it when the proxy overwrites that header on every request, or any
   client can pick its own role.
2. ``NL2SQL_API_ROLE``: one static role for every request.
3. ``user_context`` in the request body, only with the dev flag
   ``NL2SQL_API_TRUST_BODY_ROLE=true``. For local testing: it lets any client
   choose any role.

No role is a 401. The settings are read on each request.

Admin routes (anything that changes datasources, LLMs or the index) also depend
on ``require_admin``: one of the resolved roles must be in
``NL2SQL_API_ADMIN_ROLES`` (comma-separated, default ``admin``), or the request is
a 403. With none of the three role sources configured, admin routes answer
loopback clients only, so a fresh local install still works and a published port
does not hand out admin.
"""

import ipaddress
import logging
import os
from typing import Any, List

from fastapi import HTTPException, Request, status
from pydantic import ValidationError

from nl2sql import UserContext

logger = logging.getLogger(__name__)

ROLE_HEADER_ENV = "NL2SQL_API_ROLE_HEADER"
STATIC_ROLE_ENV = "NL2SQL_API_ROLE"
TRUST_BODY_ROLE_ENV = "NL2SQL_API_TRUST_BODY_ROLE"
ADMIN_ROLES_ENV = "NL2SQL_API_ADMIN_ROLES"
DEFAULT_ADMIN_ROLES = "admin"


def _body_role_trusted() -> bool:
    return os.getenv(TRUST_BODY_ROLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _roles(value: str) -> List[str]:
    return [role.strip() for role in value.split(",") if role.strip()]


def warn_if_body_role_trusted() -> None:
    """Log a warning when the dev flag lets clients choose their own role."""
    if _body_role_trusted():
        logger.warning(
            "%s is on: the RBAC role is taken from the request body, so any client "
            "can choose any role. Use it for local testing only.",
            TRUST_BODY_ROLE_ENV,
        )


async def _body_user_context(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        return None
    return body.get("user_context") if isinstance(body, dict) else None


async def get_user_context(request: Request) -> UserContext:
    """Return the caller's ``UserContext``, or raise 401 when no role is configured."""
    header = os.getenv(ROLE_HEADER_ENV, "").strip()
    if header:
        roles = _roles(request.headers.get(header, ""))
        if roles:
            return UserContext(roles=roles)

    static_role = os.getenv(STATIC_ROLE_ENV, "").strip()
    if static_role:
        return UserContext(roles=_roles(static_role))

    if _body_role_trusted():
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
            f"({ROLE_HEADER_ENV}) or a static role ({STATIC_ROLE_ENV}). The body's "
            f"user_context is ignored unless {TRUST_BODY_ROLE_ENV}=true (local testing only)."
        ),
    )



def _role_source_configured() -> bool:
    return bool(os.getenv(ROLE_HEADER_ENV, "").strip() or os.getenv(STATIC_ROLE_ENV, "").strip()
                or _body_role_trusted())


def _admin_roles() -> List[str]:
    return _roles(os.getenv(ADMIN_ROLES_ENV, "")) or [DEFAULT_ADMIN_ROLES]


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def require_admin(request: Request) -> UserContext:
    """Let the request through only if it carries an admin role; 401 with no role, 403 without admin.

    With no role source configured, only a loopback client is let through.
    """
    if not _role_source_configured():
        host = request.client.host if request.client else ""
        if _is_loopback(host):
            return UserContext(roles=_admin_roles())
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Admin routes answer only loopback clients until a role source is configured. "
                f"Set a trusted proxy header ({ROLE_HEADER_ENV}) or a static role ({STATIC_ROLE_ENV}), "
                f"and list the admin roles in {ADMIN_ROLES_ENV} (default: {DEFAULT_ADMIN_ROLES})."
            ),
        )

    user_context = await get_user_context(request)
    admin_roles = _admin_roles()
    if not set(user_context.roles) & set(admin_roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This route needs an admin role: one of {ADMIN_ROLES_ENV} ({', '.join(admin_roles)}).",
        )
    return user_context


ADMIN_RESPONSES = {
    401: {"description": "No role for this request (see the auth settings)."},
    403: {"description": f"The request has no role listed in {ADMIN_ROLES_ENV}, or no role source "
                         "is configured and the client is not on loopback."},
}
