#!/usr/bin/env python3
"""Unified CLI for the NL2SQL Ecosystem."""
import typer
import importlib.metadata
import os
import sys
import pathlib
import json
from typing import Optional, List
from typing_extensions import Annotated
from dotenv import load_dotenv

# Core Library Imports
from nl2sql.common.env_hint import active_env_file
from nl2sql.common.logger import configure_logging
from nl2sql.common.settings import reload_settings, settings
from nl2sql.context import NL2SQLContext
from nl2sql import BenchmarkConfig
from nl2sql.evaluation.gold import GOLD_DATASET_PATH

# Local CLI Imports
from nl2sql.cli.commands.indexing import run_indexing
from nl2sql.cli.commands.benchmark import run_benchmark as exec_benchmark
from nl2sql.cli.commands.run import run_pipeline 
from nl2sql.cli.commands.info import list_available_adapters
from nl2sql.cli.commands.demo import demo_command
from nl2sql.cli.commands.doctor import doctor_command
from nl2sql.cli.commands.setup import setup_command
from nl2sql.cli.commands.install import install_command
from nl2sql.cli.commands.policy import app as policy_app
from nl2sql.cli.commands.trace import app as trace_app, replay_command
from nl2sql.cli.console import configure_output_encoding
from nl2sql.cli.types import RunConfig

app = typer.Typer(
    name="nl2sql",
    help="Ask a database questions in plain English. Run `nl2sql demo` to try it.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(policy_app, name="policy", help="Manage RBAC policies and security.")
app.add_typer(trace_app, name="trace", help="Inspect and replay run traces.")

DatasourceConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--config", help="Path to datasource config YAML")]
SecretsConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--secrets-config", help="Path to secrets config YAML")]
LLMConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--llm-config", help="Path to LLM config YAML")]
VectorStoreOption = Annotated[Optional[str], typer.Option("--vector-store", help="Path to vector store directory")]


def _version_callback(value: bool) -> None:
    """Prints the installed distribution version and exits.

    Eager, so `nl2sql --version` answers without a subcommand and without
    building a context.
    """
    if value:
        typer.echo(importlib.metadata.version("nl2sql-engine"))
        raise typer.Exit()


@app.callback()
def global_callback(
    ctx: typer.Context,
    env: Annotated[Optional[str], typer.Option("--env", help="Environment name to load (.env.<name>)")] = None,
    env_file: Annotated[Optional[pathlib.Path], typer.Option("--env-file", help="Explicit path to an env file (wins over --env)")] = None,
    version: Annotated[bool, typer.Option("--version", help="Show the installed nl2sql-engine version and exit", callback=_version_callback, is_eager=True)] = False,
):
    """
    NL2SQL CLI Entry Point.
    """
    # `settings` is built when nl2sql.common.settings is first imported, which
    # happens above. Setting the variables is therefore not enough on its own;
    # the singleton has to be refreshed before any command builds a context.
    if env:
        os.environ["ENV"] = env
    if env_file:
        os.environ["ENV_FILE_PATH"] = str(env_file)
    if env or env_file:
        # pydantic-settings reads the file into `settings` only, but doctor,
        # the LLM registry and `${env:VAR}` references read os.environ. A
        # variable already exported in the shell wins over the file.
        load_dotenv(active_env_file(), override=False)
        reload_settings()

@app.command()
def run(
    query: Annotated[str, typer.Argument(help="Natural language query")],
    ds_config_path: DatasourceConfigOption = None,
    secrets_config_path: SecretsConfigOption = None,
    ds_id: Annotated[Optional[str], typer.Option(help="Target specific datasource ID")] = None,
    llm_config_path: LLMConfigOption = None,
    vector_store_path: VectorStoreOption = None,
    role: Annotated[str, typer.Option(help="Role ID for RBAC policies")] = "admin",
    no_exec: Annotated[bool, typer.Option("--no-exec", help="Skip execution (plan & validate only)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed reasoning")] = False,
    show_perf: Annotated[bool, typer.Option("--show-perf", help="Show performance metrics")] = False,
    policies_config_path: Annotated[Optional[str], typer.Option("--policies-config", help="Path to policies config")] = None,
):
    """
    Execute a query against the knowledge graph.
    """

    run_config = RunConfig(
        query=query,
        ds_id=ds_id,
        role=role,
        no_exec=no_exec,
        verbose=verbose,
        show_perf=show_perf
    )
    ctx = NL2SQLContext(ds_config_path, secrets_config_path, llm_config_path, vector_store_path, policies_config_path)

    run_pipeline(run_config, ctx)


@trace_app.command("replay")
def trace_replay(
    target: Annotated[str, typer.Argument(help="A trace file, or a trace id in TRACE_DIR")],
    ds_config_path: DatasourceConfigOption = None,
    secrets_config_path: SecretsConfigOption = None,
    llm_config_path: LLMConfigOption = None,
    vector_store_path: VectorStoreOption = None,
    policies_config_path: Annotated[Optional[pathlib.Path], typer.Option("--policies-config", help="Path to policies config")] = None,
):
    """
    Re-run a traced question feeding back the recorded LLM responses (no model calls).
    """
    replay_command(target, lambda: NL2SQLContext(ds_config_path, secrets_config_path, llm_config_path,
                                                 vector_store_path, policies_config_path))


@app.command()
def index(
    ds_config_path: DatasourceConfigOption = None,
    secrets_config_path: SecretsConfigOption = None,
    vector_store_path: VectorStoreOption = None,
    llm_config_path: LLMConfigOption = None,
    datasource: Annotated[Optional[List[str]], typer.Option(
        "--datasource", "-d",
        help="Re-index only this datasource (repeatable). Other datasources' entries are left untouched.",
    )] = None,
    full: Annotated[bool, typer.Option(
        "--full",
        help=(
            "Rebuild every datasource into a new collection and switch to it when all succeed. "
            "Needed after changing the embedding model, since every datasource must share one model."
        ),
    )] = False,
):
    """
    Index schemas and examples into the Vector Store, one datasource at a time.
    """
    ctx = NL2SQLContext(ds_config_path, secrets_config_path, llm_config_path, vector_store_path)

    run_indexing(ctx, datasource_ids=datasource or None, full=full)
    
@app.command()
def doctor():
    """
    Diagnose environment issues (Python, Packages, Connectivity).
    """
    doctor_command()


@app.command()
def setup(
    demo: Annotated[bool, typer.Option("--demo", help="Scaffold the Chinook demo project instead of running the wizard")] = False,
    api_key: Annotated[Optional[str], typer.Option("--api-key", help="API Key for LLM provider (e.g. OpenAI)")] = None,
):
    """
    Interactive setup wizard for first-time users.
    """
    setup_command(demo=demo, api_key=api_key)


@app.command()
def demo(
    directory: Annotated[pathlib.Path, typer.Option("--dir", help="Where to write the demo project (database, configs and vector store)")] = pathlib.Path("nl2sql-demo"),
    host: Annotated[str, typer.Option(help="Bind address. The default binds localhost only; 0.0.0.0 exposes the playground to your network")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to serve the playground on")] = 8765,
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Do not open a browser tab; just serve and print the URL")] = False,
    record: Annotated[bool, typer.Option("--record", help="Record the sample questions through a real provider")] = False,
    api_key: Annotated[Optional[str], typer.Option(
        "--api-key",
        help=(
            "API key for live mode, saved into the demo project's .env.demo so later runs "
            "from that directory stay live. The provider follows the key shape: an sk-or- key "
            "is OpenRouter, anything else is OpenAI. Precedence: --api-key, then "
            "OPENAI_API_KEY/OPENROUTER_API_KEY in the environment, then .env.demo, then Ollama, "
            "then replay. A key on argv is visible in shell history and to ps, so the "
            "environment variable is the more private route."
        ),
    )] = None,
    allow_settings: Annotated[bool, typer.Option(
        "--allow-settings",
        help=(
            "Turn on the playground's settings panel (API key, model per LLM node) even when "
            "--host is not a loopback address. Off by default there because the playground "
            "has no login: anyone who can reach it could swap in their own key or run up "
            "costs on yours."
        ),
    )] = False,
):
    """One-command playground over the Chinook sample database."""
    demo_command(directory, host, port, no_browser, record, api_key, allow_settings)


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

@app.command()
def benchmark(
    tier: Annotated[Optional[int], typer.Option(
        "--tier",
        help=(
            "1: run the hand-written gold plans through the validator, generator and executor "
            "with a local fake LLM (no API key). Omit to run the full pipeline with the configured LLM."
        ),
    )] = None,
    dataset: Annotated[pathlib.Path, typer.Option(help="Path to the gold dataset YAML")] = GOLD_DATASET_PATH,
    ds_config_path: DatasourceConfigOption = None,
    secrets_config_path: SecretsConfigOption = None,
    llm_config_path: LLMConfigOption = None,
    vector_store_path: VectorStoreOption = None,
    policies_config_path: Annotated[Optional[pathlib.Path], typer.Option("--policies-config", help="Path to policies config")] = None,
    bench_config_path: Annotated[Optional[pathlib.Path], typer.Option(help="Path to LLM matrix config")] = None,
    iterations: Annotated[int, typer.Option(help="Iterations per test case (tier 1 always runs once)")] = 3,
    include_ids: Annotated[Optional[List[str]], typer.Option(help="Specific Test IDs to run")] = None,
    role: Annotated[Optional[List[str]], typer.Option("--role", help="Only run as this role (repeatable)")] = None,
    export_path: Annotated[Optional[pathlib.Path], typer.Option(help="Where to write the JSON report")] = None,
):
    """
    Score the engine against the Chinook gold dataset, per question and role.

    Prints a table and a pass/fail/skip summary per role, writes a JSON report
    (benchmark_report.json unless --export-path is given) and exits 1 if any
    case failed.
    """
    bench_run_config = BenchmarkConfig(
        dataset_path=dataset,
        config_path=ds_config_path,
        bench_config_path=bench_config_path,
        llm_config_path=llm_config_path,
        iterations=iterations,
        include_ids=include_ids,
        roles=role,
        export_path=export_path,
        vector_store_path=vector_store_path,
        secrets_path=secrets_config_path,
        policies_path=policies_config_path,
    )

    exec_benchmark(bench_run_config, tier=tier)


def main():
    # The library no longer configures logging on import, so the application
    # entry point owns it: without this call the CLI emits no log output.
    configure_logging(
        level="INFO",
        json_format=(settings.observability_exporter == "otlp"),
    )
    # Before any command writes: rich emits symbols a legacy Windows code page
    # cannot encode, and an unconfigured stream turns that into a crash.
    configure_output_encoding()
    app()

if __name__ == "__main__":
    main()
