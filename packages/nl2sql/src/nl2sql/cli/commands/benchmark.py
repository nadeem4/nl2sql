import json
import os
import pathlib
import sys
from typing import Dict, List, Optional

from rich.markup import escape

from nl2sql import BenchmarkAPI, BenchmarkConfig
from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.reporting import ConsolePresenter
from nl2sql.evaluation.benchmark_runner import BenchmarkResult
from nl2sql.evaluation.records import (
    RESULTS_DIR,
    RETRIEVAL_DIR,
    current_git_commit,
    publish,
    write_records,
    write_retrieval_record,
)
from nl2sql.evaluation.retrieval_recall import compare_reports
from nl2sql.evaluation.tier2 import (
    DEFAULT_MAX_ACCURACY_DROP,
    DEFAULT_MAX_COST_INCREASE,
    check_baseline,
    unverified_models,
)

DEFAULT_REPORT_PATH = pathlib.Path("benchmark_report.json")
DEFAULT_TIER2_REPORT_PATH = pathlib.Path("benchmark_tier2.json")
DEFAULT_RETRIEVAL_REPORT_PATH = pathlib.Path("benchmark_retrieval.json")

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


def _parse_llm_specs(specs: List[str]) -> Dict[str, pathlib.Path]:
    """``NAME=PATH`` pairs, in order; raises ValueError on a malformed or repeated name."""
    parsed: Dict[str, pathlib.Path] = {}
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not sep or not name.strip() or not path.strip():
            raise ValueError(f"--llm expects NAME=PATH, got '{spec}'.")
        if name.strip() in parsed:
            raise ValueError(f"--llm names '{name.strip()}' twice.")
        parsed[name.strip()] = pathlib.Path(path.strip())
    return parsed


@handle_cli_errors
def run_tier2_benchmark(
    config: BenchmarkConfig,
    *,
    llm_specs: Optional[List[str]],
    max_cost: Optional[float],
    passes: int = 1,
    questions: Optional[List[str]] = None,
    baseline: Optional[pathlib.Path] = None,
    max_accuracy_drop: float = DEFAULT_MAX_ACCURACY_DROP,
    max_cost_increase: float = DEFAULT_MAX_COST_INCREASE,
    results_dir: pathlib.Path = RESULTS_DIR,
) -> None:
    """Runs tier 2 (the real model) per LLM config under a dollar cap and writes the scoreboard.

    Exits 0 when every config ran, 1 on a baseline regression or a failed
    run, 2 on a usage error (no ``--max-cost``, a malformed ``--llm``, or
    ``CI`` set) and 3 when the cap stopped the run early.
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
    try:
        paths = _parse_llm_specs(llm_specs or [])
    except ValueError as e:
        presenter.print_error(str(e))
        sys.exit(EXIT_USAGE)

    config = config.model_copy(update={"roles": config.roles or ["admin"], "iterations": 1})
    questions = [q.strip() for item in (questions or []) for q in item.split(",") if q.strip()]
    api = BenchmarkAPI()

    def on_case(name, record, spent):
        presenter.console.print(
            f"[{escape(name)}] pass {record['pass']} {escape(record['id'])}/{record['role']} "
            f"{record['status'].upper():4} ${record['cost']:.4f}  spent ${spent:.4f} of ${max_cost:.2f}")

    try:
        llm_configs = api.tier2_configs(config, paths or None)
        for warning in unverified_models(llm_configs):
            presenter.print_warning(warning)
        board = api.run_tier2(config, llm_configs, max_cost=max_cost, passes=passes,
                              questions=questions or None, on_case=on_case)
    except Exception as e:
        presenter.print_error(f"Benchmark Failed: {e}")
        sys.exit(1)

    for name, cfg in board["configs"].items():
        presenter.print_header(f"Tier 2 config: {name}")
        presenter.print_benchmark_results(cfg["results"])
    presenter.print_tier2_scoreboard(board)
    presenter.export_benchmark_report(board, config.export_path or DEFAULT_TIER2_REPORT_PATH)
    for path in write_records(board, results_dir, git_commit=current_git_commit()):
        presenter.print_info(f"Result record: {escape(str(path))}")

    if baseline is not None:
        problems = check_baseline(board, json.loads(pathlib.Path(baseline).read_text(encoding="utf-8")),
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
    results_dir: pathlib.Path = RETRIEVAL_DIR,
    baseline: Optional[pathlib.Path] = None,
) -> None:
    """Reports table and column recall of schema retrieval on the gold set; no key, no LLM, no cost.

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
        f"{s['tables_sent']:.1f} tables / {s['columns_sent']:.1f} columns sent on average")
    if baseline is not None:
        old = json.loads(pathlib.Path(baseline).read_text(encoding="utf-8"))
        diff = compare_reports(report, old.get("report", old))  # a report, or a record holding one
        presenter.console.print("Against baseline: " + ", ".join(f"{k} {v:+g}" for k, v in diff["summary"].items()))
        if diff["moved"]:
            presenter.print_table([[m["id"], f"{pct(m['table_recall'][0])} -> {pct(m['table_recall'][1])}",
                                    f"{pct(m['column_recall'][0])} -> {pct(m['column_recall'][1])}"]
                                   for m in diff["moved"]],
                                  title="Questions whose recall changed", columns=["ID", "Tables", "Columns"])
    presenter.export_benchmark_report(report, config.export_path or DEFAULT_RETRIEVAL_REPORT_PATH)
    if record:
        path = write_retrieval_record(report, results_dir, git_commit=current_git_commit())
        presenter.print_info(f"Result record: {escape(str(path))}")


@handle_cli_errors
def publish_benchmarks(results_dir: pathlib.Path, history_path: pathlib.Path, readme_path: pathlib.Path,
                       retrieval_dir: pathlib.Path = RETRIEVAL_DIR) -> None:
    """Rewrites the history page and the README's BENCHMARKS block from the result records."""
    count = publish(results_dir, history_path, readme_path, retrieval_dir)
    ConsolePresenter().print_success(
        f"Published {count} record(s) from {escape(str(results_dir))} and {escape(str(retrieval_dir))} "
        f"to {escape(str(history_path))} and {escape(str(readme_path))}.")
