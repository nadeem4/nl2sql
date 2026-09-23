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
from nl2sql.cli.commands.benchmark import (
    print_presets as exec_presets,
    publish_benchmarks as exec_publish,
    run_benchmark as exec_benchmark,
    run_retrieval_benchmark as exec_retrieval,
    run_tier2_benchmark as exec_tier2_benchmark,
)
from nl2sql.evaluation.records import BENCHMARKS_DIR, HISTORY_PATH, README_PATH
from nl2sql.evaluation.tier2 import DEFAULT_MAX_COST_INCREASE, DEFAULT_MAX_REGRESSIONS
from nl2sql.cli.commands.run import run_pipeline 
from nl2sql.cli.commands.info import list_available_adapters
from nl2sql.cli.commands.demo import demo_command
from nl2sql.cli.commands.doctor import doctor_command
from nl2sql.cli.commands.setup import setup_command
from nl2sql.cli.commands.install import install_command
from nl2sql.cli.commands.policy import app as policy_app
from nl2sql.cli.commands.trace import app as trace_app, replay_command
from nl2sql.cli.commands.cache import app as cache_app
from nl2sql.cli.commands.feedback import app as feedback_app
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
app.add_typer(cache_app, name="cache", help="Manage the plan cache (`nl2sql cache clear`).")
app.add_typer(feedback_app, name="feedback", help="Answer feedback and guardrail rates (`nl2sql feedback stats`).")

DatasourceConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--config", help="Path to datasource config YAML")]
SecretsConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--secrets-config", help="Path to secrets config YAML")]
LLMConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--llm-config", help="Path to LLM config YAML")]
PoliciesConfigOption = Annotated[Optional[pathlib.Path], typer.Option("--policies-config", help="Path to policies config JSON")]
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
    policies_config_path: PoliciesConfigOption = None,
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
    policies_config_path: PoliciesConfigOption = None,
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
    demo: Annotated[bool, typer.Option("--demo", help="Scaffold the demo project (chinook, support and webanalytics) instead of running the wizard")] = False,
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
            "is OpenRouter, an sk-ant- key is Anthropic, anything else is OpenAI. Precedence: --api-key, then "
            "OPENAI_API_KEY/OPENROUTER_API_KEY/ANTHROPIC_API_KEY in the environment, then .env.demo, then Ollama, "
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

benchmark_app = typer.Typer(help="Score the engine against the Chinook gold dataset; `publish` the recorded runs.")
app.add_typer(benchmark_app, name="benchmark")


@benchmark_app.command("publish")
def benchmark_publish(
    sources: Annotated[Optional[List[pathlib.Path]], typer.Option(
        "--from", help="Another project folder (repeatable): copy its records not already here into --results-dir "
                       "first. A record already here with different content is a conflict and is never overwritten.",
    )] = None,
    results_dir: Annotated[pathlib.Path, typer.Option("--results-dir", help="The benchmarks folder holding the records")] = BENCHMARKS_DIR,
    history_path: Annotated[pathlib.Path, typer.Option("--history-path", help="The history page to write")] = HISTORY_PATH,
    readme_path: Annotated[pathlib.Path, typer.Option("--readme-path", help="The README whose BENCHMARKS block is replaced")] = README_PATH,
):
    """
    Rebuild docs/benchmarks.md and the README results block from the recorded runs.

    Run from the repo root. Reads every record in benchmarks/<kind>/<database>/;
    no key, no network, and the same records always give byte-identical output.
    `--from <demo folder>` first pulls in the records a run from that folder wrote.
    Exits 1 on a conflict.
    """
    exec_publish(results_dir, history_path, readme_path, sources)


@benchmark_app.command("presets")
def benchmark_presets():
    """
    List the built-in tier 2 LLM configs (`--llm NAME`) and the model on each node.
    """
    exec_presets()


@benchmark_app.command("retrieval")
def benchmark_retrieval(
    questions: Annotated[Optional[List[str]], typer.Option(
        "--questions", help="Only these question ids or tags (repeatable, or comma-separated).",
    )] = None,
    record: Annotated[bool, typer.Option(
        "--record", help="Also write a result record under --results-dir (then `benchmark publish` from the repo).",
    )] = False,
    results_dir: Annotated[Optional[pathlib.Path], typer.Option(
        "--results-dir", help="The benchmarks folder --record writes under, as retrieval/<database>/ "
                              "(default: <project>/benchmarks).",
    )] = None,
    note: Annotated[Optional[str], typer.Option(
        "--note", help="A short label for what changed in this run, e.g. \"slim prompts\"; kept in the record.",
    )] = None,
    baseline: Annotated[Optional[pathlib.Path], typer.Option(
        "--baseline", help="An earlier report or record to compare with: prints the change in each mean and every question that moved.",
    )] = None,
    export_path: Annotated[Optional[pathlib.Path], typer.Option(help="Where to write the JSON report (default: <project>/benchmark_retrieval.json)")] = None,
    dataset: Annotated[pathlib.Path, typer.Option(help="Path to the gold dataset YAML")] = GOLD_DATASET_PATH,
    ds_config_path: DatasourceConfigOption = None,
    secrets_config_path: SecretsConfigOption = None,
    llm_config_path: LLMConfigOption = None,
    vector_store_path: VectorStoreOption = None,
    policies_config_path: PoliciesConfigOption = None,
):
    """
    Table and column recall of schema retrieval on the gold questions; no key, no LLM, no cost.

    Forces the vector search (the full-snapshot limit is set to 0), runs the
    schema retriever on each answerable question and scores what it sends
    the planner against the question's needed_tables and needed_columns.
    Report-only: exits 0 whatever the recall. <project> is the folder of the
    env file --env selects: the current folder for `--env demo`.
    """
    exec_retrieval(BenchmarkConfig(dataset_path=dataset, config_path=ds_config_path, llm_config_path=llm_config_path,
                                   export_path=export_path, vector_store_path=vector_store_path,
                                   secrets_path=secrets_config_path, policies_path=policies_config_path),
                   questions=questions, record=record, results_dir=results_dir, baseline=baseline, note=note)


@benchmark_app.callback(invoke_without_command=True)
def benchmark(
    ctx: typer.Context,
    tier:Annotated[Optional[int], typer.Option(
        "--tier",
        help=(
            "1: run the hand-written gold plans through the validator, generator and executor "
            "with a local fake LLM (no API key). 2: run the real model end to end, per --model / --llm "
            "config, under a required --max-cost cap. Omit to run the full pipeline with the configured LLM."
        ),
    )] = None,
    model: Annotated[Optional[List[str]], typer.Option(
        "--model",
        help="Tier 2: a model to put on every LLM node, named in the scoreboard by itself (repeatable). "
             "A verified model (gpt-5.4, claude-opus-5, ...) names its provider; any other is provider/model, "
             "e.g. openrouter/meta-llama/llama-3.3-70b-instruct or ollama/llama3. The key comes from the "
             "provider's variable, which --env loads.",
    )] = None,
    llm: Annotated[Optional[List[str]], typer.Option(
        "--llm",
        help="Tier 2: an LLM config to compare (repeatable): a built-in preset by name (`benchmark presets`; "
             "mini-helpers is gpt-5.4-mini-helpers), a PATH.yaml named by its stem, or NAME=PATH. "
             "Combines with --model. With neither, the project's LLM config runs as 'default'.",
    )] = None,
    note: Annotated[Optional[str], typer.Option(
        "--note", help="Tier 2: a short label for what changed in this run, e.g. \"slim prompts\"; kept in each record.",
    )] = None,
    max_cost: Annotated[Optional[float], typer.Option(
        "--max-cost", help="Tier 2 (required): stop before a question that could take total spend past this many USD.",
    )] = None,
    passes: Annotated[int, typer.Option(
        "--passes", help="Tier 2: run every question this many times per config and report determinism and "
                         "pass^N (a question counts only when every pass passed). Use --passes 3 for a baseline.",
    )] = 1,
    questions: Annotated[Optional[List[str]], typer.Option(
        "--questions", help="Tier 2: only these question ids or tags (repeatable, or comma-separated).",
    )] = None,
    baseline: Annotated[Optional[pathlib.Path], typer.Option(
        "--baseline", help="Tier 2: a committed scoreboard JSON to compare against; exits 1 on a regression.",
    )] = None,
    max_regressions: Annotated[int, typer.Option(
        "--max-regressions", help="Tier 2 baseline: how many questions may flip from pass to fail before the run "
                                  "fails. A smaller, statistically significant drop fails too.",
    )] = DEFAULT_MAX_REGRESSIONS,
    max_accuracy_drop: Annotated[Optional[float], typer.Option(
        "--max-accuracy-drop", help="Deprecated: a flat accuracy drop gate, as a fraction (0.02 = 2 points). At 43 "
                                    "questions that is less than one question, so it fires on noise. Use "
                                    "--max-regressions.",
    )] = None,
    max_cost_increase: Annotated[float, typer.Option(
        "--max-cost-increase", help="Tier 2 baseline: largest allowed rise in cost per question, as a fraction (0.2 = 20%).",
    )] = DEFAULT_MAX_COST_INCREASE,
    results_dir: Annotated[Optional[pathlib.Path], typer.Option(
        "--results-dir", help="Tier 2: the benchmarks folder one record per config is written under, as "
                              "tier2/<database>/ (default: <project>/benchmarks).",
    )] = None,
    dataset: Annotated[pathlib.Path, typer.Option(help="Path to the gold dataset YAML")] = GOLD_DATASET_PATH,
    ds_config_path: DatasourceConfigOption = None,
    secrets_config_path: SecretsConfigOption = None,
    llm_config_path: LLMConfigOption = None,
    vector_store_path: VectorStoreOption = None,
    policies_config_path: PoliciesConfigOption = None,
    bench_config_path: Annotated[Optional[pathlib.Path], typer.Option(help="Path to LLM matrix config")] = None,
    iterations: Annotated[int, typer.Option(help="Iterations per test case (tier 1 always runs once)")] = 3,
    include_ids: Annotated[Optional[List[str]], typer.Option(help="Specific Test IDs to run")] = None,
    role: Annotated[Optional[List[str]], typer.Option("--role", help="Only run as this role (repeatable; tier 2 defaults to admin)")] = None,
    export_path: Annotated[Optional[pathlib.Path], typer.Option(
        help="Where to write the JSON report (tier 2 default: <project>/benchmark_tier2.json)")] = None,
):
    """
    Score the engine against the Chinook gold dataset, per question and role.

    Prints a table and a pass/fail/skip summary per role, writes a JSON report
    (benchmark_report.json unless --export-path is given) and exits 1 if any
    case failed.

    Tier 2, from the demo folder: `nl2sql --env demo benchmark --tier 2 --model gpt-5.4 --max-cost 5`.
    It prints its plan, then writes a scoreboard comparing every --model and
    --llm config to <project>/benchmark_tier2.json and one record per config
    under <project>/benchmarks/tier2/<database>/, where <project> is the folder
    of the env file --env selects (the current folder for `--env demo`).
    Exits 0 when complete, 1 on a baseline regression, 2 without --max-cost
    and 3 when the cap stopped it early.
    """
    if ctx.invoked_subcommand:
        return
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

    if tier == 2:
        exec_tier2_benchmark(bench_run_config, llm_specs=llm, model_specs=model, max_cost=max_cost, passes=passes,
                             questions=questions, baseline=baseline, max_regressions=max_regressions,
                             max_accuracy_drop=max_accuracy_drop,
                             max_cost_increase=max_cost_increase, results_dir=results_dir, note=note)
        return
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
