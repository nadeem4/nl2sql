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
"""

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
