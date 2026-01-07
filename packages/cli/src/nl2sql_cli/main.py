#!/usr/bin/env python3
"""Unified CLI for the NL2SQL Ecosystem."""
import typer
import sys
import pathlib
import json
from typing import Optional, List
from typing_extensions import Annotated

# Core Library Imports
from nl2sql.datasources import load_profiles, DatasourceRegistry
from nl2sql.services.llm import LLMRegistry, load_llm_config
from nl2sql.common.settings import settings
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.common.logger import configure_logging

# Local CLI Imports
from nl2sql_cli.commands.indexing import run_indexing
from nl2sql_cli.commands.benchmark import run_benchmark as exec_benchmark
from nl2sql_cli.commands.run import run_pipeline as exec_pipeline
from nl2sql_cli.commands.info import list_available_adapters
from nl2sql_cli.commands.doctor import doctor_command
from nl2sql_cli.commands.setup import setup_command
from nl2sql_cli.commands.install import install_command
from nl2sql_cli.types import RunConfig, BenchmarkConfig

app = typer.Typer(
    name="nl2sql",
    help="Production-Grade Natural Language to SQL Engine.",
    no_args_is_help=True,
    add_completion=False,
)

# Shared Options
ConfigOption = Annotated[pathlib.Path, typer.Option("--config", help="Path to datasource config YAML")]
LLMConfigOption = Annotated[pathlib.Path, typer.Option("--llm-config", help="Path to LLM config YAML")]
VectorStoreOption = Annotated[str, typer.Option("--vector-store", help="Path to vector store directory")]


@app.command()
def run(
    query: Annotated[str, typer.Argument(help="Natural language query")],
    config: ConfigOption = pathlib.Path(settings.datasource_config_path),
    id: Annotated[Optional[str], typer.Option(help="Target specific datasource ID")] = None,
    llm_config: LLMConfigOption = pathlib.Path(settings.llm_config_path),
    vector_store: VectorStoreOption = settings.vector_store_path,
    user: Annotated[str, typer.Option(help="User persona for AuthZ")] = "admin",
    no_exec: Annotated[bool, typer.Option("--no-exec", help="Skip execution (plan & validate only)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed reasoning")] = False,
    show_perf: Annotated[bool, typer.Option("--show-perf", help="Show performance metrics")] = False,
):
    """
    Execute a query against the knowledge graph.
    """
    run_config = RunConfig(
        query=query,
        config_path=config,
        datasource_id=id,
        user=user,
        no_exec=no_exec,
        verbose=verbose,
        show_perf=show_perf,
        vector_store_path=vector_store
    )
    
    # Load Registry
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
    config: ConfigOption = pathlib.Path(settings.datasource_config_path),
    vector_store: VectorStoreOption = settings.vector_store_path,
    llm_config: LLMConfigOption = pathlib.Path(settings.llm_config_path),
):
    """
    Index schemas and examples into the Vector Store.
    """
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
def setup():
    """
    Interactive setup wizard for first-time users.
    """
    setup_command()


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
    config: ConfigOption = pathlib.Path(settings.datasource_config_path),
    vector_store: VectorStoreOption = settings.vector_store_path,
    bench_config: Annotated[Optional[pathlib.Path], typer.Option(help="Path to LLM matrix config")] = None,
    iterations: Annotated[int, typer.Option(help="Iterations per test case")] = 3,
    routing_only: Annotated[bool, typer.Option(help="Verify routing only, skip SQL execution")] = False,
    include_ids: Annotated[Optional[List[str]], typer.Option(help="Specific Test IDs to run")] = None,
    export_path: Annotated[Optional[pathlib.Path], typer.Option(help="Export results to JSON/CSV")] = None,
):
    """
    Run accuracy benchmarks against a golden dataset.
    """
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
    configs = load_configs(config)
    ds_registry = DatasourceRegistry(configs)
    v_store = OrchestratorVectorStore(persist_directory=vector_store)
    
    exec_benchmark(bench_run_config, ds_registry, v_store)


def main():
    app()

if __name__ == "__main__":
    main()
