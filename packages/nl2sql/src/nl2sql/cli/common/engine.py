"""The CLI's one config: the global ``--config``, and the engine every command builds from it.

``nl2sql --config prod/nl2sql.yaml <command>``. Without the flag,
``NL2SQL_CONFIG``, then ``./nl2sql.yaml`` (``nl2sql.config.load_config``). The
app callback keeps the flag in ``ctx.obj`` and loads the ``.env`` next to the
file (``nl2sql.config.load_env_file``), never overriding a variable already
set. See docs/architecture/configuration_v2.md, section 10.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer
from rich.markup import escape

from nl2sql.cli.console import console

if TYPE_CHECKING:
    from nl2sql import NL2SQL
    from nl2sql.config import Config
    from nl2sql.runtime import StorePaths


def config_option(ctx: typer.Context) -> Optional[Path]:
    """The ``--config`` the command line gave, or None for ``NL2SQL_CONFIG`` / ``./nl2sql.yaml``."""
    root = ctx.find_root()
    return (root.obj or {}).get("config") if isinstance(root.obj, dict) else None


def _fail(error: Exception) -> None:
    console.print(f"[bold red]Error:[/bold red] {escape(str(error))}", soft_wrap=True)
    raise typer.Exit(1)


def load_project_config(ctx: typer.Context) -> "Config":
    """The ``Config`` the command line names. A problem is printed and exits 1."""
    from nl2sql.config import ConfigError, load_config

    try:
        return load_config(config_option(ctx))
    except ConfigError as error:
        _fail(error)


def store_paths(ctx: typer.Context) -> "StorePaths":
    """Where the configured engine keeps its index, state, traces and results, without building it."""
    from nl2sql.runtime import StorePaths

    return StorePaths.under(load_project_config(ctx).storage.path)


def build_engine(ctx: typer.Context, *, check: bool = True) -> "NL2SQL":
    """The engine the command line's config describes.

    With ``check``, ``engine.check()`` runs first: a ``required`` datasource
    that fails, or no datasource ready at all, prints the report and exits 1;
    an optional one that fails prints the report and the command runs
    degraded.
    """
    from nl2sql import NL2SQL, ConfigError, EngineNotReady
    from nl2sql.common.logger import configure_logging

    try:
        engine = NL2SQL(load_project_config(ctx))
    except ConfigError as error:
        _fail(error)
    if engine.config.settings.observability.exporter == "otlp":
        configure_logging(json_format=True)
    if not check:
        return engine
    try:
        report = engine.check()
    except EngineNotReady as error:
        engine.close()
        _fail(error)
    failed = [d for d in report.datasources if d.status == "failed"]
    if failed:
        console.print("[yellow]Running degraded: these datasources are unavailable.[/yellow]")
        for status in failed:
            console.print(escape(str(status)), soft_wrap=True)
    return engine
