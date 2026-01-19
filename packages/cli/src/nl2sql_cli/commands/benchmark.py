import sys
import yaml
from nl2sql.llm import LLMRegistry
from nl2sql.datasources import DatasourceRegistry
from nl2sql.indexing.vector_store import VectorStore
from nl2sql.evaluation.evaluator import ModelEvaluator
from nl2sql.reporting import ConsolePresenter
from nl2sql.runners.benchmark_runner import BenchmarkRunner
from nl2sql_cli.types import BenchmarkConfig
from nl2sql_cli.common.decorators import handle_cli_errors


@handle_cli_errors
def run_benchmark(
    config: BenchmarkConfig, 
    datasource_registry: DatasourceRegistry, 
    vector_store: VectorStore
) -> None:
    """Runs the benchmark suite based on provided arguments.

    Args:
        config (BenchmarkConfig): Benchmark run configuration.
        datasource_registry (DatasourceRegistry): Datasource registry.
        vector_store (VectorStore): Vector store instance.
    """
    presenter = ConsolePresenter()
    
    # Matrix Benchmarking
    llm_configs = {}

    if config.bench_config_path and config.bench_config_path.exists():
        try:
            bench_data = yaml.safe_load(config.bench_config_path.read_text()) or {}
            from nl2sql.configs import ConfigManager
            cm = ConfigManager()
            for name, cfg_data in bench_data.items():
                if isinstance(cfg_data, dict):
                     # Benchmark config often has sub-hashes of LLM configs
                     llm_configs[name] = cm.load_llm_from_data(cfg_data)
        except Exception as e:
            presenter.print_error(f"Error reading bench config: {e}")
            sys.exit(1)
    
    if not llm_configs:
        llm_cfg = parse_llm_config({"default": {"provider": "openai", "model": "gpt-4o"}}) # Fallback
        if config.llm_config_path and config.llm_config_path.exists():
            from nl2sql.services.llm import load_llm_config
            llm_cfg = load_llm_config(config.llm_config_path)
            
        if config.stub_llm:
            llm_cfg.default.provider = "stub"
            for agent_cfg in llm_cfg.agents.values():
                agent_cfg.provider = "stub"
                
        llm_configs["default"] = llm_cfg

    # Run Matrix
    for name, llm_cfg in llm_configs.items():
        llm_registry = LLMRegistry(llm_cfg)
        
        presenter = ConsolePresenter()
        presenter.print_header(f"Evaluating Config: {name}")

        runner = BenchmarkRunner(config, datasource_registry, vector_store, llm_registry)

        try:
            result = runner.run_dataset(
                config_name=name, 
                progress_callback=presenter.track 
            )
            
            # Delegate Reporting
            presenter.print_dataset_benchmark_results(result.results, iterations=result.iterations, routing_only=config.routing_only)
            
            # Print Summary
            presenter.print_metrics_summary(result.metrics, result.results, routing_only=config.routing_only)

            if config.export_path:
                presenter.export_results(result.results, config.export_path)

        except Exception as e:
            presenter.print_error(f"Benchmark Failed: {e}")
            sys.exit(1)
