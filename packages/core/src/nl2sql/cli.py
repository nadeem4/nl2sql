#!/usr/bin/env python3
"""
CLI entrypoint for the NL2SQL LangGraph pipeline.
"""
import argparse
import pathlib
import sys
from typing import Any, Dict, List

from nl2sql.datasources import load_profiles, DatasourceRegistry
from nl2sql.services.llm import LLMRegistry, load_llm_config
from nl2sql.common.settings import settings
from nl2sql.services.vector_store import OrchestratorVectorStore

# Import commands
from nl2sql.commands.indexing import run_indexing
from nl2sql.commands.benchmark import run_benchmark
from nl2sql.commands.run import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NL2SQL LangGraph pipeline.")
    parser.add_argument("--config", type=pathlib.Path, default=pathlib.Path(settings.datasource_config_path))
    parser.add_argument("--id", type=str, default=None, help="Datasource profile id (default: auto-route)")
    parser.add_argument("--query", type=str, help="User NL query (if omitted, you will be prompted)")
    parser.add_argument("--llm-config", type=pathlib.Path, default=pathlib.Path(settings.llm_config_path), help="Path to LLM config YAML (provider/model per agent)")
    parser.add_argument("--vector-store", type=str, default=settings.vector_store_path, help="Path to vector store directory")
    parser.add_argument("--index", action="store_true", help="Index the schema into the vector store")
    parser.add_argument("--stub-llm", action="store_true", help="Use a stub LLM that returns a fixed plan")
    parser.add_argument("--no-exec", action="store_true", help="Skip execution (generate/validate only)")
    parser.add_argument("--verbose", action="store_true", help="Show reasoning thoughts and step-by-step info")
    parser.add_argument("--debug", action="store_true", help="Enable full debug mode (outputs, logs, traces)")
    parser.add_argument("--show-perf", action="store_true", help="Display performance metrics (tokens/latency)")

    
    # Benchmarking args
    parser.add_argument("--benchmark", action="store_true", help="Run in benchmark mode")
    parser.add_argument("--dataset", type=pathlib.Path, default=None, help="Path to golden dataset YAML for accuracy evaluation")
    parser.add_argument("--routing-only", action="store_true", help="Benchmark: Verify datasource routing only, skip execution")
    parser.add_argument("--matrix", action="store_true", help="Benchmark: Run with multiple LLMs (uses defaults from benchmark_suite.yaml if --bench-config is not provided)")
    parser.add_argument("--bench-config", type=pathlib.Path, default=None, help="Path to a single YAML file containing multiple named LLM configs (optional)")
    parser.add_argument("--iterations", type=int, default=3, help="Number of iterations per config (benchmark mode only)")
    parser.add_argument("--include-ids", nargs="+", default=None, help="Benchmark: List of specific test IDs to run (space separated)")
    parser.add_argument("--export-path", type=pathlib.Path, default=None, help="Benchmark: Export results to file (.json or .csv)")

    parser.add_argument("--list-adapters", action="store_true", help="List all installed datasource adapters")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Immediate actions that don't need full config loading
    if args.list_adapters:
        from nl2sql.commands.info import list_available_adapters
        list_available_adapters()
        return

    from nl2sql.common.logger import configure_logging
    
    level = "CRITICAL" 
    
    if args.debug:
        level = "DEBUG"
    elif args.verbose:
        level = "INFO"
        
    configure_logging(level=level, json_format=False)

    profiles = load_profiles(args.config)

    # Initialize Registries
    llm_cfg = load_llm_config(args.llm_config)
    llm_registry = LLMRegistry(llm_cfg)
    
    datasource_registry = DatasourceRegistry(profiles)

    vector_store = OrchestratorVectorStore(persist_directory=args.vector_store)

    if args.index:
        run_indexing(profiles, args.vector_store, vector_store, llm_registry)
        if not args.query:
            print("Indexing complete.")
            return

    if vector_store.is_empty():
        print(f"Error: Vector store at '{args.vector_store}' is empty or not initialized.", file=sys.stderr)
        print("Please run indexing first:", file=sys.stderr)
        print(f"  python -m src.nl2sql.cli --index --vector-store {args.vector_store}", file=sys.stderr)
        sys.exit(1)

    # Benchmark Mode
    if args.benchmark:
        if not args.dataset:
             print("Error: --dataset is required for benchmarking.", file=sys.stderr)
             print("To benchmark a single query, add it to a dataset and use --include-ids.", file=sys.stderr)
             sys.exit(1)
             
        if args.matrix and not args.bench_config:
            args.bench_config = pathlib.Path(settings.benchmark_config_path)

        run_benchmark(args, datasource_registry, vector_store)
        return

    query = args.query

    if not query:
        # Interactive mode
        print("Enter your query (or 'exit' to quit):")
        try:
            query = input("> ")
            if query.lower() in ["exit", "quit"]:
                return
        except KeyboardInterrupt:
            return

    run_pipeline(args, query, datasource_registry, llm_registry, vector_store)

if __name__ == "__main__":
    main()
