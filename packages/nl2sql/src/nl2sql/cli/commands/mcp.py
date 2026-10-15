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

    Raises:
        ValueError: no role and no default role (and not ``allow_all``), or a
            role the policies do not define.
    """
    from nl2sql import UserContext

    if not policies.allow_all:
        unknown = [role for role in roles if role not in policies.roles]
        if unknown:
            raise ValueError(f"Unknown role(s) {', '.join(unknown)}. The roles are: "
                             f"{', '.join(policies.roles) or '(none)'}.")
        roles = roles or ([policies.default_role] if policies.default_role else [])
        if not roles:
            raise ValueError("nl2sql mcp needs a role: pass --role <role> (repeatable), or set "
                             "policies.default_role in nl2sql.yaml. A caller with no role reads nothing.")
    return UserContext(user_id=user, roles=roles)


def mcp_command(ctx: typer.Context, roles: List[str], user: Optional[str]) -> None:
    """Build the engine and serve it over stdio until the client closes the stream."""
    from nl2sql import NL2SQL, ConfigError, EngineNotReady
    from nl2sql.config import load_config

    try:
        config = load_config(config_option(ctx))
        identity = caller(config.policies, list(roles), user)
    except (ConfigError, ValueError) as error:
        _fail(str(error))
    try:
        from nl2sql.mcp import server
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
        logger.info("Serving %d datasource(s) over stdio as role(s) %s", len(config.datasources),
                    ", ".join(identity.roles) or "(allow_all)")
        server.build_server(engine, identity).run("stdio")
    except EngineNotReady as error:
        _fail(str(error))
    finally:
        engine.close()
