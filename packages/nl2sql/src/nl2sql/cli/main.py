#!/usr/bin/env python3
"""Unified CLI for the NL2SQL Ecosystem."""
import typer
import importlib.metadata
import pathlib
from typing import Optional, List
from typing_extensions import Annotated

# Core Library Imports
from nl2sql.common.logger import configure_logging
from nl2sql.config import load_env_file
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
from nl2sql.cli.commands.demo import DEFAULT_QUESTIONS_PER_MINUTE, DEFAULT_QUESTIONS_PER_SESSION, demo_command
from nl2sql.cli.commands.doctor import doctor_command
from nl2sql.cli.commands.init import init_command
from nl2sql.cli.commands.install import install_command
from nl2sql.cli.commands.policy import app as policy_app
from nl2sql.cli.commands.curated import app as curated_app
from nl2sql.cli.commands.trace import app as trace_app, replay_command
from nl2sql.cli.commands.cache import app as cache_app
from nl2sql.cli.commands.feedback import app as feedback_app
from nl2sql.cli.common.engine import build_engine
from nl2sql.cli.console import configure_output_encoding
from nl2sql.cli.types import RunConfig

app = typer.Typer(
    name="nl2sql",
    help="Ask a database questions in plain English. Run `nl2sql demo` to try it.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(policy_app, name="policy", help="Manage RBAC policies and security.")
app.add_typer(curated_app, name="curated", help="The curated answers file: `nl2sql curated validate`, `nl2sql curated test`, `nl2sql curated proposals export`.")
app.add_typer(trace_app, name="trace", help="Inspect and replay run traces.")
app.add_typer(cache_app, name="cache", help="Manage the plan cache (`nl2sql cache clear`).")
app.add_typer(feedback_app, name="feedback", help="Answer feedback and guardrail rates (`nl2sql feedback stats`).")


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
    version: Annotated[bool, typer.Option("--version", help="Show the installed nl2sql-engine version and exit", callback=_version_callback, is_eager=True)] = False,
):
    """
    NL2SQL CLI Entry Point.
    """
    ctx.obj = {"config": config}
    load_env_file(config)


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
    target: Annotated[str, typer.Argument(help="A trace file, or a trace id in <storage.path>/traces")],
):
    """
    Re-run a traced question feeding back the recorded LLM responses (no model calls).
    """
    replay_command(target, lambda: build_engine(ctx).context, ctx)


@app.command()
def index(
    ctx: typer.Context,
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
    Index schemas and examples into the Vector Store, one datasource at a time.
    """
    if full and curated_only:
        raise typer.BadParameter("--full and --curated-only cannot be combined.")
    if enrich and curated_only:
        raise typer.BadParameter("--enrich and --curated-only cannot be combined.")
    run_indexing(build_engine(ctx), enrich=enrich, datasource_ids=datasource or None, full=full,
                 curated_only=curated_only)


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
    ctx: typer.Context,
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
):
    """
    Table and column recall of schema retrieval on the gold questions; no key, no LLM, no cost.

    Forces the vector search (the full-snapshot limit is set to 0), runs the
    schema retriever on each answerable question and scores what it sends
    the planner against the question's needed_tables and needed_columns.
    Report-only: exits 0 whatever the recall. <project> is the folder of
    nl2sql.yaml.
    """
    exec_retrieval(ctx, BenchmarkConfig(dataset_path=dataset, export_path=export_path),
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
             "provider's variable, which the .env next to nl2sql.yaml may set.",
    )] = None,
    llm: Annotated[Optional[List[str]], typer.Option(
        "--llm",
        help="Tier 2: an LLM config to compare (repeatable): a built-in preset by name (`benchmark presets`; "
             "mini-helpers is gpt-5.4-mini-helpers), a PATH.yaml holding an llm section (as llm.yaml does) named "
             "by its stem, or NAME=PATH. Combines with --model. With neither, the config's llm section runs as "
             "'default'.",
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
        "--max-accuracy-drop", help="Deprecated: a flat accuracy drop gate, as a fraction (0.02 = 2 points). At 47 "
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
    use_curated: Annotated[bool, typer.Option(
        "--use-curated", help="Tier 2: keep curated answers on, so a verified query may answer a gold question. "
                              "Off by default, as in tier 1: each case records its answer's source and the "
                              "scoreboard splits accuracy by it.",
    )] = False,
    dataset: Annotated[pathlib.Path, typer.Option(help="Path to the gold dataset YAML")] = GOLD_DATASET_PATH,
    bench_config_path: Annotated[Optional[pathlib.Path], typer.Option(
        help="LLM configs to compare: a YAML mapping each name to an llm section")] = None,
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

    Tier 2, from the demo folder: `nl2sql benchmark --tier 2 --model gpt-5.4 --max-cost 5`.
    It prints its plan, then writes a scoreboard comparing every --model and
    --llm config to <project>/benchmark_tier2.json and one record per config
    under <project>/benchmarks/tier2/<database>/, where <project> is the folder
    of nl2sql.yaml.
    Exits 0 when complete, 1 on a baseline regression, 2 without --max-cost
    and 3 when the cap stopped it early.
    """
    if ctx.invoked_subcommand:
        return
    bench_run_config = BenchmarkConfig(
        dataset_path=dataset,
        bench_config_path=bench_config_path,
        iterations=iterations,
        include_ids=include_ids,
        roles=role,
        export_path=export_path,
    )

    if tier == 2:
        exec_tier2_benchmark(ctx, bench_run_config, llm_specs=llm, model_specs=model, max_cost=max_cost, passes=passes,
                             questions=questions, baseline=baseline, max_regressions=max_regressions,
                             max_accuracy_drop=max_accuracy_drop,
                             max_cost_increase=max_cost_increase, results_dir=results_dir, note=note,
                             use_curated=use_curated)
        return
    exec_benchmark(ctx, bench_run_config, tier=tier)


def main():
    # The library no longer configures logging on import, so the application
    # entry point owns it: without this call the CLI emits no log output. A
    # command that builds the engine switches to JSON logs when its config's
    # settings.observability.exporter is otlp (cli/common/engine.py).
    configure_logging(level="INFO")
    # Before any command writes: rich emits symbols a legacy Windows code page
    # cannot encode, and an unconfigured stream turns that into a crash.
    configure_output_encoding()
    app()

if __name__ == "__main__":
    main()
