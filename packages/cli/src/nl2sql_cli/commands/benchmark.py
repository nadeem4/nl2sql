import sys
from nl2sql import BenchmarkAPI, BenchmarkConfig
from nl2sql_cli.reporting import ConsolePresenter
from nl2sql_cli.common.decorators import handle_cli_errors


@handle_cli_errors
def run_benchmark(
    config: BenchmarkConfig,
) -> None:
    """Runs the benchmark suite based on provided arguments.

    Args:
        config (BenchmarkConfig): Benchmark run configuration.
    """
    presenter = ConsolePresenter()
    api = BenchmarkAPI()

    try:
        matrix_result = api.run_matrix(config, progress_callback=presenter.track)
    except Exception as e:
        presenter.print_error(f"Benchmark Failed: {e}")
        sys.exit(1)

    for name, result in matrix_result.results_by_config.items():
        presenter.print_header(f"Evaluating Config: {name}")
        presenter.print_dataset_benchmark_results(
            result.results,
            iterations=result.iterations,
            routing_only=config.routing_only,
        )
        presenter.print_metrics_summary(result.metrics, result.results, routing_only=config.routing_only)
        if config.export_path:
            presenter.export_results(result.results, config.export_path)
