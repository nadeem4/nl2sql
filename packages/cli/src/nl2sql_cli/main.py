#!/usr/bin/env python3
"""Unified CLI for the NL2SQL Ecosystem."""
import typer
import sys
import pathlib
import json
from typing import Optional, List
from typing_extensions import Annotated

# Core Library Imports
from nl2sql.datasources import DatasourceRegistry
from nl2sql.datasources.config import load_configs
from nl2sql.services.llm import LLMRegistry, load_llm_config
from nl2sql.common.settings import settings
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.common.logger import configure_logging
from nl2sql.secrets import secret_manager, load_secret_configs, SecretManager

# Local CLI Imports
from nl2sql_cli.commands.indexing import run_indexing
from nl2sql_cli.commands.benchmark import run_benchmark as exec_benchmark
from nl2sql_cli.commands.run import run_pipeline as exec_pipeline
from nl2sql_cli.commands.info import list_available_adapters
from nl2sql_cli.commands.doctor import doctor_command
from nl2sql_cli.commands.setup import setup_command
from nl2sql_cli.commands.install import install_command
from nl2sql_cli.commands.policy import app as policy_app
from nl2sql_cli.types import RunConfig, BenchmarkConfig

app = typer.Typer(
    name="nl2sql",
    help="Production-Grade Natural Language to SQL Engine.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(policy_app, name="policy", help="Manage RBAC policies and security.")

# Shared Options
# Shared Options
ConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--config", help="Path to datasource config YAML")]
SecretsConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--secrets-config", help="Path to secrets config YAML")]
LLMConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--llm-config", help="Path to LLM config YAML")]
VectorStoreOption = Annotated[Optional[str], typer.Option("--vector-store", help="Path to vector store directory")]


@app.callback()
def global_callback(
    ctx: typer.Context,
    env: Annotated[Optional[str], typer.Option("--env", "-e", help="Environment name (e.g. dev, prod). Isolation for configs/data.")] = None,
):
    """
    NL2SQL CLI Entry Point.
    """
    if env:
        settings.configure_env(env)


@app.command()
def run(
    query: Annotated[str, typer.Argument(help="Natural language query")],
    config: ConfigOption = None,
    secrets_config: SecretsConfigOption = None,
    id: Annotated[Optional[str], typer.Option(help="Target specific datasource ID")] = None,
    llm_config: LLMConfigOption = None,
    vector_store: VectorStoreOption = None,
    role: Annotated[str, typer.Option(help="Role ID for RBAC policies")] = "admin",
    no_exec: Annotated[bool, typer.Option("--no-exec", help="Skip execution (plan & validate only)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed reasoning")] = False,
    show_perf: Annotated[bool, typer.Option("--show-perf", help="Show performance metrics")] = False,
):
    """
    Execute a query against the knowledge graph.
    """
    # Resolve Paths (Callback may have updated settings)
    if config is None:
        config = pathlib.Path(settings.datasource_config_path)
    if secrets_config is None:
        secrets_config = pathlib.Path(settings.secrets_config_path)
    if llm_config is None:
        llm_config = pathlib.Path(settings.llm_config_path)
    if vector_store is None:
        vector_store = settings.vector_store_path

    run_config = RunConfig(
        query=query,
        config_path=config,
        datasource_id=id,
        role=role,
        no_exec=no_exec,
        verbose=verbose,
        show_perf=show_perf,
        vector_store_path=vector_store
    )
    
    # Load Registry
    
    # 1. Load Secrets FIRST (Datasources depend on them)
    secret_configs = load_secret_configs(secrets_config)
    secret_manager.configure(secret_configs)
    
    # 2. Load Datasources
    configs = load_configs(config)
    ds_registry = DatasourceRegistry(configs)
    
    try:
        llm_cfg = load_llm_config(llm_config)
        llm_registry = LLMRegistry(llm_cfg)
    except Exception:
        llm_registry = LLMRegistry(None)

    v_store = OrchestratorVectorStore(persist_directory=vector_store)

    exec_pipeline(run_config, ds_registry, llm_registry, v_store)


@app.command()
def index(
    config: ConfigOption = None,
    secrets_config: SecretsConfigOption = None,
    vector_store: VectorStoreOption = None,
    llm_config: LLMConfigOption = None,
):
    """
    Index schemas and examples into the Vector Store.
    """
    # Resolve Paths
    if config is None:
        config = pathlib.Path(settings.datasource_config_path)
    if secrets_config is None:
        secrets_config = pathlib.Path(settings.secrets_config_path)
    if llm_config is None:
        llm_config = pathlib.Path(settings.llm_config_path)
    if vector_store is None:
        vector_store = settings.vector_store_path

    # Load Secrets FIRST
    secret_configs = load_secret_configs(secrets_config)
    secret_manager.configure(secret_configs)

    configs = load_configs(config)
    
    try:
        llm_cfg = load_llm_config(llm_config)
        llm_registry = LLMRegistry(llm_cfg)
    except Exception:
        llm_registry = None

    v_store = OrchestratorVectorStore(persist_directory=vector_store)
    
    run_indexing(configs, vector_store, v_store, llm_registry)


@app.command()
def doctor():
    """
    Diagnose environment issues (Python, Packages, Connectivity).
    """
    doctor_command()


@app.command()
def setup(
    demo: Annotated[bool, typer.Option("--demo", help="Quickstart specific demo environment")] = False,
    docker: Annotated[bool, typer.Option("--docker", help="Use Docker for demo (Full fidelity)")] = False,
):
    """
    Interactive setup wizard for first-time users.
    """
    # If Docker provided, lite is False. If not provided, lite is True (default).
    lite = not docker
    setup_command(demo=demo, lite=lite, docker=docker)


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
    dataset: Annotated[pathlib.Path, typer.Option(help="Path to golden dataset YAML")],
    config: ConfigOption = None,
    secrets_config: SecretsConfigOption = None,
    vector_store: VectorStoreOption = None,
    bench_config: Annotated[Optional[pathlib.Path], typer.Option(help="Path to LLM matrix config")] = None,
    iterations: Annotated[int, typer.Option(help="Iterations per test case")] = 3,
    routing_only: Annotated[bool, typer.Option(help="Verify routing only, skip SQL execution")] = False,
    include_ids: Annotated[Optional[List[str]], typer.Option(help="Specific Test IDs to run")] = None,
    export_path: Annotated[Optional[pathlib.Path], typer.Option(help="Export results to JSON/CSV")] = None,
):
    """
    Run accuracy benchmarks against a golden dataset.
    """
    # Resolve Paths
    if config is None:
        config = pathlib.Path(settings.datasource_config_path)
    if secrets_config is None:
        secrets_config = pathlib.Path(settings.secrets_config_path)
    if vector_store is None:
        vector_store = settings.vector_store_path

    bench_run_config = BenchmarkConfig(
        dataset_path=dataset,
        config_path=config,
        bench_config_path=bench_config,
        llm_config_path=None, # Matrix uses bench_config
        iterations=iterations,
        routing_only=routing_only,
        include_ids=include_ids,
        export_path=export_path,
        vector_store_path=vector_store,
        stub_llm=False
    )
    
    # Load Registry (Configs -> Eager Adapters)
    secret_configs = load_secret_configs(secrets_config)
    secret_manager.configure(secret_configs)

    configs = load_configs(config)
    ds_registry = DatasourceRegistry(configs)
    v_store = OrchestratorVectorStore(persist_directory=vector_store)
    
    exec_benchmark(bench_run_config, ds_registry, v_store)


def main():
    app()

if __name__ == "__main__":
    main()
