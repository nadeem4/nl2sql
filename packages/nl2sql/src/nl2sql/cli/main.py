#!/usr/bin/env python3
"""Unified CLI for the NL2SQL Ecosystem."""
import click
import typer
import importlib.metadata
import pathlib
from typing import Optional, List
from typing_extensions import Annotated

# Core Library Imports
from nl2sql.common.logger import configure_logging
from nl2sql.config import load_env_file

# Local CLI Imports
from nl2sql.cli.commands.indexing import run_indexing
from nl2sql.cli.commands.run import run_pipeline
from nl2sql.cli.commands.info import list_available_adapters
from nl2sql.cli.commands.demo import DEFAULT_QUESTIONS_PER_MINUTE, DEFAULT_QUESTIONS_PER_SESSION, demo_command
from nl2sql.cli.commands.doctor import doctor_command
from nl2sql.cli.commands.init import init_command
from nl2sql.cli.commands.install import install_command
from nl2sql.cli.commands.policy import app as policy_app
from nl2sql.cli.commands.curated import app as curated_app
from nl2sql.cli.commands.trace import app as trace_app, replay_command
from nl2sql.cli.commands.cache import app as cache_app
from nl2sql.cli.commands.config_check import app as config_app
from nl2sql.cli.commands.feedback import app as feedback_app
from nl2sql.cli.commands.eval import app as eval_app
from nl2sql.cli.commands.storage import app as storage_app
from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import configure_output_encoding
from nl2sql.cli.types import RunConfig

app = typer.Typer(
    name="nl2sql",
    help="Ask a database questions in plain English. Run `nl2sql demo` to try it.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(config_app, name="config", help="Check the config without starting the engine (`nl2sql config check`).")
app.add_typer(policy_app, name="policy", help="Manage RBAC policies and security.")
app.add_typer(curated_app, name="curated", help="The curated answers file: `nl2sql curated validate`, `nl2sql curated test`, `nl2sql curated proposals export`.")
app.add_typer(trace_app, name="trace", help="Inspect and replay run traces.")
app.add_typer(cache_app, name="cache", help="Manage the plan cache (`nl2sql cache clear`).")
app.add_typer(feedback_app, name="feedback", help="Answer feedback and guardrail rates (`nl2sql feedback stats`).")
app.add_typer(storage_app, name="storage", help="Retention for traces and results (`nl2sql storage purge`).")
app.add_typer(eval_app, name="eval", help="Evals: `nl2sql eval run` (with the regression gate), `regenerate`, `publish`.")


def _version_callback(value: bool) -> None:
    """Prints the installed distribution version and exits.

    Eager, so `nl2sql --version` answers without a subcommand and without
    reading a config.
    """
    if value:
        typer.echo(importlib.metadata.version("nl2sql-engine"))
        raise typer.Exit()


@app.callback()
def global_callback(
    ctx: typer.Context,
    config: Annotated[Optional[pathlib.Path], typer.Option(
        "--config",
        help="The nl2sql.yaml to read. Without it, NL2SQL_CONFIG, then ./nl2sql.yaml. "
             "The .env next to it is loaded, never overriding a variable already set.",
    )] = None,
    log_format: Annotated[Optional[str], typer.Option(
        "--log-format",
        help="Log lines as text or json (one JSON object per line, with its trace_id). "
             "Without it, NL2SQL_LOG_FORMAT, then text.",
        click_type=click.Choice(["text", "json"]),
    )] = None,
    version: Annotated[bool, typer.Option("--version", help="Show the installed nl2sql-engine version and exit", callback=_version_callback, is_eager=True)] = False,
):
    """
    NL2SQL CLI Entry Point.
    """
    ctx.obj = {"config": config}
    load_env_file(config)
    # After the .env, so a NL2SQL_LOG_LEVEL or NL2SQL_LOG_FORMAT written there
    # applies. The library never configures logging on import: the entry
    # point owns it.
    try:
        configure_logging(json_format=None if log_format is None else log_format == "json")
    except ValueError as error:  # NL2SQL_LOG_FORMAT is neither text nor json
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(1)


@app.command()
def run(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Natural language query")],
    ds_id: Annotated[Optional[str], typer.Option(help="Target specific datasource ID")] = None,
    role: Annotated[str, typer.Option(help="Role ID for RBAC policies")] = "admin",
    no_exec: Annotated[bool, typer.Option("--no-exec", help="Skip execution (plan & validate only)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed reasoning")] = False,
    show_perf: Annotated[bool, typer.Option("--show-perf", help="Show performance metrics")] = False,
):
    """
    Answer a question against the configured datasources.
    """

    run_config = RunConfig(
        query=query,
        ds_id=ds_id,
        role=role,
        no_exec=no_exec,
        verbose=verbose,
        show_perf=show_perf
    )
    run_pipeline(run_config, build_engine(ctx))


@trace_app.command("replay")
def trace_replay(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="A trace file, or a trace id in the trace store")],
):
    """
    Re-run a traced question feeding back the recorded LLM responses (no model calls).
    """
    replay_command(target, lambda: build_engine(ctx), ctx)


index_app = typer.Typer(help="Index schemas and verified queries, one datasource at a time; `nl2sql index prune`.")
app.add_typer(index_app, name="index")


@index_app.callback(invoke_without_command=True)
def index(
    ctx: typer.Context,
    datasource: Annotated[Optional[List[str]], typer.Option(
        "--datasource", "-d",
        help="Re-index only this datasource (repeatable). Other datasources' entries are left untouched.",
    )] = None,
    curated_only: Annotated[bool, typer.Option(
        "--curated-only",
        help=(
            "Re-index only the verified queries from curated.yaml. Each datasource's schema entries "
            "are kept as they are: no schema read and no enrichment model call."
        ),
    )] = False,
    enrich: Annotated[bool, typer.Option(
        "--enrich",
        help=(
            "Ask the indexing_enrichment LLM to write table and column descriptions where the "
            "database has none. Spends tokens; off by default."
        ),
    )] = False,
):
    """
    Index schemas and examples into the index of the configured embedding model, one datasource at a time.
    """
    if ctx.invoked_subcommand is not None:
        return
    if enrich and curated_only:
        raise typer.BadParameter("--enrich and --curated-only cannot be combined.")
    run_indexing(build_engine(ctx), enrich=enrich, datasource_ids=datasource or None, curated_only=curated_only)


@index_app.command("prune")
def index_prune(ctx: typer.Context):
    """
    Drop the index of every other embedding model, including 0.5's collection. The current one is kept.
    """
    from nl2sql.cli.common.indexing import run_prune

    run_prune(build_engine(ctx, check=False))


@app.command()
def doctor(ctx: typer.Context):
    """
    Diagnose the install: Python, packages and adapters, provider keys, and the configured engine's readiness.
    """
    doctor_command(ctx)


@app.command()
def init(
    directory: Annotated[pathlib.Path, typer.Argument(help="The folder to write the config into")] = pathlib.Path("."),
    demo: Annotated[bool, typer.Option("--demo", help="Write the demo: three sample SQLite databases, their roles and verified queries")] = False,
    provider: Annotated[str, typer.Option("--provider", help="The LLM provider llm.yaml names, with its default model")] = "openai",
    force: Annotated[bool, typer.Option("--force", help="Overwrite files that already exist")] = False,
):
    """
    Write a new config: nl2sql.yaml and one file per section. Never writes a key.
    """
    init_command(directory, demo=demo, provider=provider, force=force)


@app.command()
def demo(
    directory: Annotated[pathlib.Path, typer.Option("--dir", help="Where to write the demo project (`nl2sql init --demo`: nl2sql.yaml, its files, the databases and the index)")] = pathlib.Path("nl2sql-demo"),
    host: Annotated[str, typer.Option(help="Bind address. The default binds localhost only; 0.0.0.0 exposes the playground to your network")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to serve the playground on")] = 8765,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Do not open a browser tab; just serve and print the URL")] = False,
    record: Annotated[bool, typer.Option("--record", help="Record the sample questions through a real provider")] = False,
    api_key: Annotated[Optional[str], typer.Option(
        "--api-key",
        help=(
            "API key for live mode, saved into the demo project's .env so later runs "
            "from that directory stay live. The provider follows the key shape: an sk-or- key "
            "is OpenRouter, an sk-ant- key is Anthropic, anything else is OpenAI. Precedence: --api-key, then "
            "OPENAI_API_KEY, OPENROUTER_API_KEY or ANTHROPIC_API_KEY in the environment, then .env, then Ollama, "
            "then replay. A key on argv is visible in shell history and to ps, so the "
            "environment variable is the more private route."
        ),
    )] = None,
    allow_settings: Annotated[bool, typer.Option(
        "--allow-settings",
        help=(
            "Turn on the playground's settings panel (API key, model per LLM node), Rebuild "
            "and the Retrieval inspector even when --host is not a loopback address. Off by "
            "default there because the playground has no login: anyone who can reach it could "
            "swap in their own key, run up costs on yours, or read every index entry, column "
            "statistics and sample values included."
        ),
    )] = False,
    hosted: Annotated[bool, typer.Option(
        "--hosted",
        help=(
            "Serve a public demo of the sample databases. The server holds no API key: every "
            "visitor pastes their own, the browser keeps it and sends it with each question, and "
            "it is used in memory for that question only -- never written to a file, an "
            "environment variable, a log or a trace. Settings, Rebuild, feedback and --record are "
            "refused, the sample databases are opened read-only, and questions are rate limited "
            "per visitor and capped per session (--questions-per-minute, --questions-per-session)."
        ),
    )] = False,
    questions_per_minute: Annotated[int, typer.Option(
        "--questions-per-minute", min=1, help="Hosted mode: how many questions a minute one visitor may ask.",
    )] = DEFAULT_QUESTIONS_PER_MINUTE,
    questions_per_session: Annotated[int, typer.Option(
        "--questions-per-session", min=1, help="Hosted mode: how many questions one browser session may ask.",
    )] = DEFAULT_QUESTIONS_PER_SESSION,
):
    """One-command playground over the demo's sample databases (`nl2sql init --demo` into --dir)."""
    demo_command(directory, host, port, no_browser, record, api_key, allow_settings, hosted,
                 questions_per_minute=questions_per_minute, questions_per_session=questions_per_session)


@app.command()
def install(package: str):
    """
    Helper to install adapter packages (e.g. 'postgres').
    """
    install_command(package)


@app.command("list-adapters")
def list_adapters():
    """
    List all installed datasource adapters.
    """
    list_available_adapters()


def main():
    # Logging is configured in global_callback, once the .env is loaded.
    # Before any command writes: rich emits symbols a legacy Windows code page
    # cannot encode, and an unconfigured stream turns that into a crash.
    configure_output_encoding()
    app()

if __name__ == "__main__":
    main()
