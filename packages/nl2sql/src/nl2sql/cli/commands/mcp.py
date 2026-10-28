"""``nl2sql mcp``: serve the engine to an MCP client over stdio (wave_a.md, section 12).

stdio has no authentication: whoever started the process is the caller. Its
role comes from ``--role``, else ``policies.default_role``; with neither the
server refuses to start, since a caller with no role reads nothing (D33).
stdout is the protocol, so everything this command says goes to stderr.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

import typer

from nl2sql.cli.common.engine import config_option

if TYPE_CHECKING:
    from nl2sql import UserContext
    from nl2sql.config import Policies

logger = logging.getLogger(__name__)


def _fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


def caller(policies: "Policies", roles: List[str], user: Optional[str]) -> "UserContext":
    """The ``UserContext`` the server answers for: ``roles``, else the default role.

    Under ``allow_all`` no role is needed (every caller reads everything), and
    none is defined, so any ``--role`` is unknown. A ``default_role`` that
    names no role never gets here: the config does not load.

    Raises:
        ValueError: a role the policies do not define, or no role and no
            default role (and not ``allow_all``).
    """
    from nl2sql import UserContext

    unknown = [role for role in roles if role not in policies.roles]
    if unknown:
        defined = ", ".join(policies.roles) or "none (policies.allow_all is set)"
        raise ValueError(f"Unknown role(s) {', '.join(unknown)}. The roles are: {defined}.")
    if not policies.allow_all:
        roles = roles or ([policies.default_role] if policies.default_role else [])
        if not roles:
            raise ValueError("nl2sql mcp needs a role: pass --role <role> (repeatable), or set "
                             "policies.default_role in nl2sql.yaml. A caller with no role reads nothing.")
    return UserContext(user_id=user, roles=roles)


def mcp_command(ctx: typer.Context, roles: List[str], user: Optional[str], http: bool = False,
                host: Optional[str] = None, port: Optional[int] = None) -> None:
    """Build the engine and serve it: over stdio until the client closes the stream, or with ``http`` over HTTP
    until stopped. HTTP with ``mcp.auth: api_keys`` takes each caller from its key, so no role is given."""
    from nl2sql import NL2SQL, ConfigError, EngineNotReady
    from nl2sql.config import load_config

    try:
        config = load_config(config_option(ctx))
        if http and config.mcp.auth == "api_keys":
            if roles or user:
                raise ValueError("--role and --user do not apply with mcp.auth: api_keys: each API key carries its "
                                 "user and roles (nl2sql keys create).")
            identity = None
        else:
            identity = caller(config.policies, list(roles), user)
    except (ConfigError, ValueError) as error:
        _fail(str(error))
    try:
        from nl2sql.mcp import server
        from nl2sql.mcp import http as http_server
    except ImportError:
        _fail('nl2sql mcp needs the mcp extra: pip install "nl2sql-engine[mcp]"')

    try:
        engine = NL2SQL(config)
    except ConfigError as error:
        _fail(str(error))
    try:
        report = engine.check()
        for status in report.datasources:
            if status.status == "failed":
                logger.warning("Running degraded: %s", status)
        if config.policies.allow_all:
            logger.warning("policies.allow_all is set: the MCP client can read every table of every datasource.")
        if http:
            logger.info("Serving %d datasource(s) over HTTP with %s", len(config.datasources),
                        "API keys" if identity is None else f"no keys, as role(s) {', '.join(identity.roles) or '(allow_all)'}")
            http_server.serve(engine, identity, host=host, port=port)
        else:
            logger.info("Serving %d datasource(s) over stdio as role(s) %s", len(config.datasources),
                        ", ".join(identity.roles) or "(allow_all)")
            server.build_server(engine, identity).run("stdio")
    except (EngineNotReady, ValueError) as error:
        _fail(str(error))
    finally:
        engine.close()
