import json
import os
import pathlib
import sys
from typing import Dict, List, Optional, Sequence, Union

from rich.markup import escape

from nl2sql import BenchmarkAPI, BenchmarkConfig
from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.reporting import ConsolePresenter
from nl2sql.common.env_hint import active_env_file
from nl2sql.configs.llm import LLMFileConfig
from nl2sql.evaluation import presets
from nl2sql.evaluation.benchmark_runner import BenchmarkResult
from nl2sql.evaluation.gold import load_gold_dataset
from nl2sql.evaluation.records import (
    BENCHMARKS_DIR,
    import_records,
    publish,
    write_records,
    write_retrieval_record,
)
from nl2sql.evaluation.retrieval_recall import compare_reports
from nl2sql.evaluation.tier2 import (
    DEFAULT_MAX_COST_INCREASE,
    DEFAULT_MAX_REGRESSIONS,
    check_baseline,
    compare_with_baseline,
    node_agents,
    select_question_ids,
    unverified_models,
)
from nl2sql.llm.providers import LLM_AGENTS

DEFAULT_REPORT_PATH = pathlib.Path("benchmark_report.json")
DEFAULT_TIER2_REPORT_PATH = pathlib.Path("benchmark_tier2.json")
DEFAULT_RETRIEVAL_REPORT_PATH = pathlib.Path("benchmark_retrieval.json")


def project_dir() -> pathlib.Path:
    """The project a run belongs to: the folder of the env file ``--env``/``--env-file`` selects.

    ``--env demo`` reads ``./.env.demo``, so it is the current folder; an
    ``--env-file`` elsewhere names that file's folder.
    """
    return pathlib.Path(active_env_file()).resolve().parent

# Tier 2 exit codes beyond 0 (complete) and 1 (baseline regression or a failed run).
EXIT_USAGE = 2
EXIT_STOPPED_AT_MAX_COST = 3


@handle_cli_errors
def run_benchmark(config: BenchmarkConfig, tier: Optional[int] = None) -> None:
    """Runs the benchmark, prints it, writes the JSON report and exits non-zero on any failure.

    Args:
        config: Benchmark run configuration.
        tier: ``1`` runs the hand-written gold plans through the code nodes
            with a local fake LLM (no API key). ``None`` runs the full
            pipeline with the configured LLM. Tier 2 is :func:`run_tier2_benchmark`.
    """
    presenter = ConsolePresenter()
    if tier not in (None, 1):
        presenter.print_error(f"Unknown tier {tier}. Use --tier 1 or --tier 2, or omit it to run with the configured LLM.")
        sys.exit(EXIT_USAGE)

    api = BenchmarkAPI()
    try:
        if tier == 1:
            results: Dict[str, BenchmarkResult] = {"tier1": api.run_tier1(config)}
        else:
            results = api.run_matrix(config, progress_callback=presenter.track).results_by_config
    except Exception as e:
        presenter.print_error(f"Benchmark Failed: {e}")
        sys.exit(1)

    for name, result in results.items():
        presenter.print_header(f"Evaluating Config: {name}")
        presenter.print_benchmark_results(result.results)
        presenter.print_benchmark_summary(result.metrics)

    report = {
        "tier": tier,
        "dataset": str(config.dataset_path),
        "configs": {name: {"summary": r.metrics, "results": r.results} for name, r in results.items()},
    }
    presenter.export_benchmark_report(report, config.export_path or DEFAULT_REPORT_PATH)

    if any(r.metrics["total"]["fail"] for r in results.values()):
        sys.exit(1)


def _llm_choices(model_specs: Sequence[str], llm_specs: Sequence[str]
                 ) -> Dict[str, Union[pathlib.Path, LLMFileConfig]]:
    """The configs to compare, by name: each ``--model``, then each ``--llm``; ValueError on a bad one."""
    choices: Dict[str, Union[pathlib.Path, LLMFileConfig]] = {}

    def add(name, cfg):
        if name in choices:
            raise ValueError(f"Two configs are named '{name}'; give one a NAME=PATH name.")
        choices[name] = cfg

    for spec in model_specs:
        add(spec.strip(), presets.model_config(spec.strip()))
    for spec in llm_specs:
        add(*presets.resolve_llm_spec(spec))
    return choices


def _node_models(cfg: LLMFileConfig) -> str:
    """``gpt-5.4 (every node)``, or each model with the nodes it runs."""
    by_model: Dict[str, List[str]] = {}
    for node, agent in node_agents(cfg).items():
        by_model.setdefault(agent.model, []).append(LLM_AGENTS[node])
    if len(by_model) == 1:
        return f"{next(iter(by_model))} (every node)"
    return "; ".join(f"{m} ({', '.join(sorted(nodes))})" for m, nodes in sorted(by_model.items()))


def _question_count(config: BenchmarkConfig, questions: Sequence[str]) -> int:
    dataset = load_gold_dataset(config.dataset_path)
    return len(select_question_ids(dataset, questions)) if questions else len(dataset)


def print_presets() -> None:
    """Lists the built-in ``--llm`` presets with the model on each LLM node."""
    console = ConsolePresenter().console
    console.print("Tier 2 presets (--llm NAME):")
    for name, cfg in presets.list_presets():
        console.print(escape(f"  {name}: {_node_models(cfg)}"), soft_wrap=True)
    console.print(escape(f"Files: {presets.PRESETS_DIR}"), soft_wrap=True)


@handle_cli_errors
def run_tier2_benchmark(
    config: BenchmarkConfig,
    *,
    llm_specs: Optional[List[str]],
    max_cost: Optional[float],
    model_specs: Optional[List[str]] = None,
    passes: int = 1,
    questions: Optional[List[str]] = None,
    baseline: Optional[pathlib.Path] = None,
    max_regressions: int = DEFAULT_MAX_REGRESSIONS,
    max_accuracy_drop: Optional[float] = None,
    max_cost_increase: float = DEFAULT_MAX_COST_INCREASE,
    results_dir: Optional[pathlib.Path] = None,
    note: Optional[str] = None,
) -> None:
    """Runs tier 2 (the real model) per LLM config under a dollar cap and writes the scoreboard.

    The scoreboard goes to ``<project>/benchmark_tier2.json`` and one record
    per config under ``<project>/benchmarks/tier2/<database>/`` unless
    ``--export-path`` / ``--results-dir`` say otherwise. Exits 0 when every
    config ran, 1 on a baseline regression or a failed run, 2 on a usage
    error (no ``--max-cost``, a bad ``--model`` or ``--llm``, or ``CI`` set)
    and 3 when the cap stopped the run early.
    """
    presenter = ConsolePresenter()
    if os.environ.get("CI"):
        presenter.print_error("Tier 2 calls a paid model and never runs in CI (CI is set).")
        sys.exit(EXIT_USAGE)
    if max_cost is None or max_cost <= 0:
        presenter.print_error("Tier 2 calls a paid model: give a dollar cap with --max-cost, e.g. --max-cost 5.")
        sys.exit(EXIT_USAGE)
    if passes < 1:
        presenter.print_error("--passes must be at least 1.")
        sys.exit(EXIT_USAGE)
    questions = [q.strip() for item in (questions or []) for q in item.split(",") if q.strip()]
    try:
        choices = _llm_choices(model_specs or [], llm_specs or [])
        question_count = _question_count(config, questions)
    except ValueError as e:
        presenter.print_error(str(e))
        sys.exit(EXIT_USAGE)

    project = project_dir()
    results_dir = pathlib.Path(results_dir) if results_dir else project / BENCHMARKS_DIR
    export_path = pathlib.Path(config.export_path) if config.export_path else project / DEFAULT_TIER2_REPORT_PATH
    config = config.model_copy(update={"roles": config.roles or ["admin"], "iterations": 1})
    api = BenchmarkAPI()

    def on_case(name, record, spent):
        presenter.console.print(escape(
            f"[{name}] pass {record['pass']} {record['id']}/{record['role']} "
            f"{record['status'].upper():4} ${record['cost']:.4f}  spent ${spent:.4f} of ${max_cost:.2f}"))

    try:
        llm_configs = api.tier2_configs(config, choices or None)
        for warning in unverified_models(llm_configs):
            presenter.print_warning(warning)
        plan = [f"Plan: {question_count} question{'s' if question_count != 1 else ''} x role "
                f"{', '.join(config.roles)} x {passes} pass{'es' if passes > 1 else ''}, cap ${max_cost:.2f}"]
        plan += [f"  {name}: {_node_models(cfg)}" for name, cfg in llm_configs.items()]
        plan.append(f"  Records: {results_dir / 'tier2'}  Scoreboard: {export_path}")
        for line in plan:
            presenter.console.print(escape(line), soft_wrap=True)
        board = api.run_tier2(config, llm_configs, max_cost=max_cost, passes=passes,
                              questions=questions or None, on_case=on_case)
    except Exception as e:
        presenter.print_error(f"Benchmark Failed: {e}")
        sys.exit(1)

    for name, cfg in board["configs"].items():
        presenter.print_header(f"Tier 2 config: {name}")
        presenter.print_benchmark_results(cfg["results"])
    presenter.print_tier2_scoreboard(board)
    presenter.export_benchmark_report(board, export_path)
    for path in write_records(board, results_dir, note=note):
        presenter.print_info(f"Result record: {escape(str(path))}")

    if baseline is not None:
        if max_accuracy_drop is not None:
            presenter.print_warning(
                "--max-accuracy-drop is deprecated: at 43 questions two points is less than one question, "
                "so it fires on noise. The gate is --max-regressions plus McNemar's test.")
        old = json.loads(pathlib.Path(baseline).read_text(encoding="utf-8"))
        presenter.print_baseline_comparison(compare_with_baseline(board, old))
        problems = check_baseline(board, old, max_regressions=max_regressions,
                                  max_accuracy_drop=max_accuracy_drop, max_cost_increase=max_cost_increase)
        for problem in problems:
            presenter.print_error(f"Regression: {problem}")
        if problems:
            sys.exit(1)
        presenter.print_success(f"No regression against {baseline}.")

    if board.get("stopped"):
        presenter.print_warning(f"Stopped early ({board['stopped']}): the scoreboard is partial.")
        sys.exit(EXIT_STOPPED_AT_MAX_COST)


@handle_cli_errors
def run_retrieval_benchmark(
    config: BenchmarkConfig,
    *,
    questions: Optional[List[str]] = None,
    record: bool = False,
    results_dir: Optional[pathlib.Path] = None,
    baseline: Optional[pathlib.Path] = None,
    note: Optional[str] = None,
) -> None:
    """Reports table and column recall of schema retrieval on the gold set; no key, no LLM, no cost.

    The report goes to ``<project>/benchmark_retrieval.json`` and, with
    ``record``, a record under ``<project>/benchmarks/retrieval/<database>/``.
    Report-only: exits 0 whatever the recall, 1 only if the run failed.
    """
    presenter = ConsolePresenter()
    questions = [q.strip() for item in (questions or []) for q in item.split(",") if q.strip()]
    try:
        report = BenchmarkAPI().run_retrieval(config, questions or None)
    except Exception as e:
        presenter.print_error(f"Retrieval benchmark failed: {e}")
        sys.exit(1)

    def pct(v):
        return "-" if v is None else f"{v:.1%}"

    results = sorted(report["results"], key=lambda r: r["id"])
    presenter.print_table(
        [[r["id"], pct(r["table_recall"]), pct(r["column_recall"]), r["tables_sent"], r["columns_sent"],
          ", ".join(r["missed_columns"]) or "-"] for r in results],
        title="Schema retrieval recall", columns=["ID", "Tables", "Columns", "Tables sent", "Columns sent", "Missed"])
    s, st = report["summary"], report["settings"]
    presenter.console.print(
        f"{s['questions']} questions, k {st['table_k']} tables / {st['planning_k']} planning, {escape(st['embedding'])}: "
        f"table recall {pct(s['table_recall'])} ({s['perfect_tables']} complete), "
        f"column recall {pct(s['column_recall'])} ({s['perfect_columns']} complete), "
        f"{s['tables_sent']:.1f} tables / {s['columns_sent']:.1f} columns sent on average, "
        f"datasource top-1 {pct(s.get('datasource_top1_accuracy'))}")
    if baseline is not None:
        old = json.loads(pathlib.Path(baseline).read_text(encoding="utf-8"))
        diff = compare_reports(report, old.get("report", old))  # a report, or a record holding one
        presenter.console.print("Against baseline: " + ", ".join(f"{k} {v:+g}" for k, v in diff["summary"].items()))
        if diff["moved"]:
            presenter.print_table([[m["id"], f"{pct(m['table_recall'][0])} -> {pct(m['table_recall'][1])}",
                                    f"{pct(m['column_recall'][0])} -> {pct(m['column_recall'][1])}"]
                                   for m in diff["moved"]],
                                  title="Questions whose recall changed", columns=["ID", "Tables", "Columns"])
    project = project_dir()
    presenter.export_benchmark_report(
        report, pathlib.Path(config.export_path) if config.export_path else project / DEFAULT_RETRIEVAL_REPORT_PATH)
    if record:
        path = write_retrieval_record(report, pathlib.Path(results_dir) if results_dir else project / BENCHMARKS_DIR,
                                      note=note)
        presenter.print_info(f"Result record: {escape(str(path))}")


@handle_cli_errors
def publish_benchmarks(results_dir: pathlib.Path, history_path: pathlib.Path, readme_path: pathlib.Path,
                       sources: Optional[List[pathlib.Path]] = None) -> None:
    """Copies in any new records from ``sources``, then rewrites the history page and the README block.

    Exits 1 when a source record clashes with a different record already
    here (the one here is kept; the pages are still rebuilt).
    """
    presenter = ConsolePresenter()
    conflicts = []
    if sources:
        outcome = import_records(sources, results_dir)
        for rel in outcome["copied"]:
            presenter.print_info(f"Copied {escape(rel)}")
        if outcome["identical"]:
            presenter.print_info(f"{len(outcome['identical'])} record(s) already here, unchanged.")
        for path in outcome["old_format"]:
            presenter.print_warning(f"Skipped {escape(path)}: written before the current record layout.")
        conflicts = outcome["conflicts"]
        for rel in conflicts:
            presenter.print_error(f"Conflict: {escape(rel)} is already here with different content; kept the one here.")
    count = publish(results_dir, history_path, readme_path)
    presenter.print_success(f"Published {count} record(s) from {escape(str(results_dir))} "
                            f"to {escape(str(history_path))} and {escape(str(readme_path))}.")
    if conflicts:
        sys.exit(1)
