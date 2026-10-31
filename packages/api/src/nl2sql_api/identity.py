"""Who is asking (docs/architecture/wave_d.md, D203 and D205).

``api.auth`` in ``nl2sql.yaml`` picks one source and every request resolves one ``UserContext`` through it:

* ``api_keys``: ``Authorization: Bearer <token>``, verified by ``engine.api_keys.verify``.
* ``proxy``: the user, tenant and roles headers of a proxy that connects from ``api.proxy.trusted_from``.
* ``static``: one role (``api.static_role``) for every request.
* ``none`` (the default): the local operator, only for a loopback request. ``serve`` refuses to bind any other host
  (:func:`check_bind`), and a request whose connecting peer or ``Host`` is not loopback, or that was forwarded, is
  refused, so neither a published container port, a same-host reverse proxy nor a DNS-rebinding page passes for the
  operator.

``caller`` is the dependency a route takes for the request's ``UserContext``; ``admin`` adds the check that one of its
roles is in ``policies.admin_roles``. Neither ever reads the request body.
"""
import ipaddress
from typing import List

from fastapi import Depends, Request

from nl2sql import NL2SQL, UserContext
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE, RequestRefused

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")

AUTH_RESPONSES = {401: {**ERROR_RESPONSE, "description": "No identity: see `api.auth`."}}
ADMIN_RESPONSES = {**AUTH_RESPONSES,
                   403: {**ERROR_RESPONSE, "description": "The caller holds no role in `policies.admin_roles`."}}


class StartRefused(Exception):
    """The server would start in a configuration that is not safe; the message says what to change."""


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(auth: str, host: str) -> None:
    """D205: with ``api.auth: none`` the server binds a loopback host only.

    Raises:
        StartRefused: ``auth`` is ``none`` and ``host`` is not loopback.
    """
    if auth == "none" and not _is_loopback(host.strip("[]")):
        raise StartRefused(
            f"api.auth is none, which is for the local operator only, and {host or 'that host'} is not a loopback "
            "address. Bind 127.0.0.1, or set api.auth to api_keys, proxy or static in nl2sql.yaml.")


def _roles(value: str) -> List[str]:
    return [role.strip() for role in value.split(",") if role.strip()]


def _unauthenticated(message: str, fix: str, *, bearer: bool = False) -> RequestRefused:
    return RequestRefused(401, "UNAUTHENTICATED", message, fix=fix,
                          headers={"WWW-Authenticate": "Bearer"} if bearer else None)


def _request_host(request: Request) -> str:
    """The ``Host`` header without its port."""
    host = request.headers.get("host", "").strip()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _api_keys(request: Request, engine: NL2SQL) -> UserContext:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    user = engine.api_keys.verify(token.strip()) if scheme.lower() == "bearer" and token.strip() else None
    if user is None:
        raise _unauthenticated("A valid API key is required.",
                               "Send Authorization: Bearer <key>. Create a key with `nl2sql keys create`.",
                               bearer=True)
    return user


def _proxy(request: Request, engine: NL2SQL) -> UserContext:
    config = engine.config
    proxy = config.api.proxy
    peer = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(peer)
        trusted = any(address in ipaddress.ip_network(cidr, strict=False) for cidr in proxy.trusted_from)
    except ValueError:
        trusted = False
    user_id = request.headers.get(proxy.user_header, "").strip() if trusted else ""
    if not user_id:
        raise _unauthenticated(
            "No identity for this request.",
            f"The request must come through the proxy ({', '.join(proxy.trusted_from)}) with the "
            f"{proxy.user_header} header set. Change api.proxy.trusted_from if the proxy connects from elsewhere.")
    tenant = request.headers.get(proxy.tenant_header, "").strip() if proxy.tenant_header else ""
    return UserContext(user_id=user_id, tenant_id=tenant or config.settings.tenant_id,
                       roles=_roles(request.headers.get(proxy.roles_header, "")))


def _static(engine: NL2SQL) -> UserContext:
    role = (engine.config.api.static_role or "").strip()
    if not role:
        raise _unauthenticated("No role is configured for this server.",
                               "Set api.static_role in nl2sql.yaml, or choose another api.auth.")
    return UserContext(user_id="static", roles=_roles(role))


def _operator(request: Request, engine: NL2SQL) -> UserContext:
    peer = request.client.host if request.client else ""
    if (not _is_loopback(peer) or _request_host(request) not in LOOPBACK_HOSTS
            or any(h in request.headers for h in FORWARDING_HEADERS)):
        raise RequestRefused(
            403, "FORBIDDEN", "api.auth is none, which answers only requests made directly to localhost.",
            fix="Set api.auth to api_keys, proxy or static in nl2sql.yaml to serve anyone else.")
    return UserContext(user_id="local", roles=list(engine.config.policies.admin_roles))


def caller(request: Request, engine: NL2SQL = Depends(get_engine)) -> UserContext:
    """The request's ``UserContext`` from the source ``api.auth`` names; 401 when there is none, 403 under ``none``
    for a request that is not the local operator."""
    auth = engine.config.api.auth
    if auth == "api_keys":
        return _api_keys(request, engine)
    if auth == "proxy":
        return _proxy(request, engine)
    if auth == "static":
        return _static(engine)
    return _operator(request, engine)


def is_admin(user: UserContext, engine: NL2SQL) -> bool:
    """Whether ``user`` holds a role in ``policies.admin_roles``."""
    return bool(set(user.roles) & set(engine.config.policies.admin_roles))


def admin(user: UserContext = Depends(caller), engine: NL2SQL = Depends(get_engine)) -> UserContext:
    """As ``caller``, and the caller must hold one of ``policies.admin_roles``; 403 otherwise."""
    policies = engine.config.policies
    if not is_admin(user, engine):
        raise RequestRefused(
            403, "FORBIDDEN", "This route needs an admin role.",
            fix=f"Give the caller one of policies.admin_roles ({', '.join(policies.admin_roles) or 'none defined'}).")
    return user


def is_local_operator(engine: NL2SQL) -> bool:
    """Whether callers who pass :func:`admin` are the local operator (``api.auth: none``). A literal secret typed into
    a Connect form may be written to the ``.env`` beside ``nl2sql.yaml`` only then (D122)."""
    return engine.config.api.auth == "none"
