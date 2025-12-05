#!/usr/bin/env python3
"""
CLI entrypoint for the NL2SQL LangGraph pipeline.
"""
import argparse
import pathlib
import sys
from typing import Any, Dict, List

from nl2sql.datasource_config import load_profiles
from nl2sql.llm_registry import LLMRegistry, load_llm_config
from nl2sql.settings import settings
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.datasource_registry import DatasourceRegistry

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
    parser.add_argument("--verbose", action="store_true", help="Show raw planner/generator outputs")
    parser.add_argument("--debug", action="store_true", help="Show output of each node in the graph")
    parser.add_argument("--show-thoughts", action="store_true", help="Show step-by-step reasoning from AI nodes")
    parser.add_argument("--json-logs", action="store_true", help="Enable structured JSON logging")
    parser.add_argument("--log-level", type=str, default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Set the logging level")
    parser.add_argument("--show-perf", action="store_true", help="Show performance metrics (latency)")
    parser.add_argument("--visualize", action="store_true", help="Visualize execution trace (dynamic)")
    
    # Benchmarking args
    parser.add_argument("--benchmark", action="store_true", help="Run in benchmark mode")
    parser.add_argument("--dataset", type=pathlib.Path, default=None, help="Path to golden dataset YAML for accuracy evaluation")
    parser.add_argument("--bench-config", type=pathlib.Path, default=pathlib.Path(settings.benchmark_config_path), help="Path to a single YAML file containing multiple named LLM configs (required if --benchmark is set)")
    parser.add_argument("--iterations", type=int, default=3, help="Number of iterations per config (benchmark mode only)")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    from nl2sql.logger import configure_logging
    
    level = "CRITICAL" 
    
    if args.debug:
        level = "DEBUG"
    elif args.log_level:
        level = args.log_level
    elif args.json_logs:
        level = "INFO" 
        
    configure_logging(level=level, json_format=args.json_logs)

    profiles = load_profiles(args.config)

    # Initialize Registries
    llm_cfg = load_llm_config(args.llm_config)
    llm_registry = LLMRegistry(llm_cfg)
    
    datasource_registry = DatasourceRegistry(profiles)

    vector_store = SchemaVectorStore(persist_directory=args.vector_store)

    if args.index:
        run_indexing(profiles, args.vector_store, vector_store)
        if not args.query:
            print("Indexing complete.")
            return

    if vector_store.is_empty():
        print(f"Error: Vector store at '{args.vector_store}' is empty or not initialized.", file=sys.stderr)
        print("Please run indexing first:", file=sys.stderr)
        print(f"  python -m src.nl2sql.cli --index --vector-store {args.vector_store}", file=sys.stderr)
        sys.exit(1)

    # Benchmark Mode (Dataset or Config)
    if args.benchmark:
        # If dataset is provided, query is not required
        if args.dataset:
             run_benchmark(args, None, datasource_registry, vector_store)
             return
        # If config is provided, query is required (for single query benchmark)
        elif args.query:
             run_benchmark(args, args.query, datasource_registry, vector_store)
             return
        else:
             print("Error: --query is required for config-based benchmark.", file=sys.stderr)
             sys.exit(1)

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





    # Run Standard Pipeline
    run_pipeline(args, query, datasource_registry, llm_registry, vector_store)

if __name__ == "__main__":
    main()
