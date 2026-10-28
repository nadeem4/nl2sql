"""MCP over HTTP: the server of ``server.py`` behind bearer API keys (docs/architecture/wave_c.md, section 9).

``build_app(engine, identity)`` returns an ASGI app serving ``mcp.http.path`` over the SDK's streamable HTTP
transport, stateless with JSON responses: each request stands alone, and a follow-up names its
``conversation_id``. ``serve`` runs it with Uvicorn.

With ``mcp.auth: api_keys`` every request carries ``Authorization: Bearer <token>``; ``engine.api_keys.check``
turns it into the key's own ``UserContext``, which a context variable holds for that request, and the tools read
it through the ``identity`` callable ``build_server`` takes. A refused request is a 401 and an ``mcp.denied``
event with the reason and, for a revoked key, its id: never the token. With ``auth: none`` every request runs as
the ``identity`` given, and only a loopback address may be bound. TLS is a reverse proxy's or a tunnel's job.
"""
from __future__ import annotations

import ipaddress
import logging
from contextvars import ContextVar
from typing import Optional

import anyio
import uvicorn
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from nl2sql import NL2SQL, UserContext

from . import server

logger = logging.getLogger(__name__)

# The caller of the request being served. A context variable, not a global: each request runs in its own task,
# so two requests in flight never see each other's caller.
_caller: ContextVar[Optional[UserContext]] = ContextVar("nl2sql_mcp_caller", default=None)


def _identity() -> UserContext:
    user = _caller.get()
    if user is None:  # fail closed: a tool never runs without a caller
        raise RuntimeError("No caller was set for this request.")
    return user


def build_app(engine: NL2SQL, identity: Optional[UserContext]) -> ASGIApp:
    """The ASGI app for ``engine``'s ``mcp`` section. Its lifespan must run (Uvicorn does) for requests to work.

    ``identity`` is who every request runs as when ``mcp.auth`` is ``none``; with ``api_keys`` it is not used.

    Raises:
        ValueError: ``mcp.auth`` is ``none`` and no ``identity`` was given, or ``http.allowed_hosts`` names a
            host that is not loopback (a proxy or tunnel forwarding to this loopback bind would expose the one
            identity).
    """
    fixed = identity if engine.config.mcp.auth == "none" else None
    if engine.config.mcp.auth == "none" and fixed is None:
        raise ValueError("mcp.auth is none: pass the identity every request runs as (nl2sql mcp --http --role ROLE).")
    if fixed is not None:
        public = [h for h in engine.config.mcp.http.allowed_hosts if not _loopback(_host_of(h))]
        if public:
            raise ValueError(f"mcp.auth is none and mcp.http.allowed_hosts lists {', '.join(public)}: a proxy or "
                             f"tunnel for that name would let anyone read as the one identity. Set auth: api_keys "
                             f"in mcp.yaml, or list only loopback names.")
    app = server.build_server(engine, _identity, over_http=True).streamable_http_app()
    return _Authenticated(app, engine, fixed)


def serve(engine: NL2SQL, identity: Optional[UserContext], *, host: Optional[str] = None,
          port: Optional[int] = None) -> None:
    """Serve ``engine`` over HTTP until stopped; ``host`` and ``port`` default to ``mcp.http``.

    Raises:
        ValueError: ``mcp.auth`` is ``none`` and ``host`` is not a loopback address, or ``build_app``'s.
    """
    config = engine.config.mcp
    host = host or config.http.host
    port = port or config.http.port
    if config.auth == "none" and not _loopback(host):
        raise ValueError(f"mcp.auth is none and {host} is not a loopback address: anyone who can reach it would "
                         f"read as the one identity. Set auth: api_keys in mcp.yaml (and make keys with "
                         f"`nl2sql keys create`), or bind 127.0.0.1.")
    _run(build_app(engine, identity), host, port)


def _run(app: ASGIApp, host: str, port: int) -> None:
    uvicorn.run(app, host=host, port=port, log_level="info")


def _host_of(pattern: str) -> str:
    """The host of an ``allowed_hosts`` entry: ``localhost:*`` -> ``localhost``, ``[::1]:*`` -> ``::1``."""
    if pattern.startswith("["):
        return pattern[1:pattern.index("]")] if "]" in pattern else pattern
    return pattern.rsplit(":", 1)[0] if pattern.count(":") == 1 else pattern


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class _Authenticated:
    """Checks the bearer key before the MCP app sees the request, and names the caller for it."""

    def __init__(self, app: ASGIApp, engine: NL2SQL, fixed: Optional[UserContext]):
        self._app = app
        self._engine = engine
        self._fixed = fixed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":  # a websocket, or anything else: never served, so never unauthenticated
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        user = self._fixed
        if user is None:
            user, refusal = await self._authenticate(scope)
            if refusal is not None:
                await refusal(scope, receive, send)
                return
        marker = _caller.set(user)
        try:
            await self._app(scope, receive, send)
        finally:
            _caller.reset(marker)

    async def _authenticate(self, scope: Scope):
        """``(user, None)`` for a good key, else ``(None, the response to send)``."""
        header = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"authorization"), None)
        if header is None:
            return None, await self._refuse("missing")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None, await self._refuse("malformed")
        try:
            checked = await anyio.to_thread.run_sync(self._engine.api_keys.check, token.strip())
        except Exception:
            logger.exception("The API key check failed")  # the message never carries the token
            return None, JSONResponse({"error": "The server could not check the key."}, status_code=500)
        if checked.user is None:
            return None, await self._refuse(checked.reason, checked.key_id)
        return checked.user, None

    async def _refuse(self, reason: str, key_id: Optional[str] = None) -> JSONResponse:
        await anyio.to_thread.run_sync(self._engine.api_keys.record_denied, reason, key_id)
        return JSONResponse({"error": "A valid API key is required: send Authorization: Bearer <token>."},
                            status_code=401, headers={"WWW-Authenticate": "Bearer"})
