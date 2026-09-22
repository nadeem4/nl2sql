import pathlib
import sys
from typing import Dict, Optional

from nl2sql import BenchmarkAPI, BenchmarkConfig
from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.reporting import ConsolePresenter
from nl2sql.evaluation.benchmark_runner import BenchmarkResult

DEFAULT_REPORT_PATH = pathlib.Path("benchmark_report.json")


@handle_cli_errors
def run_benchmark(config: BenchmarkConfig, tier: Optional[int] = None) -> None:
    """Runs the benchmark, prints it, writes the JSON report and exits non-zero on any failure.

    Args:
        config: Benchmark run configuration.
        tier: ``1`` runs the hand-written gold plans through the code nodes
            with a local fake LLM (no API key). ``None`` runs the full
            pipeline with the configured LLM.
    """
    presenter = ConsolePresenter()
    if tier not in (None, 1):
        presenter.print_error(f"Unknown tier {tier}. Use --tier 1, or omit it to run with the configured LLM.")
        sys.exit(2)

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
