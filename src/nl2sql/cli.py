#!/usr/bin/env python3
"""
CLI entrypoint for the NL2SQL LangGraph pipeline.
"""
import argparse
import pathlib
import sys
import statistics
from typing import Any, Dict, List

from nl2sql.datasource_config import get_profile, load_profiles
from nl2sql.langgraph_pipeline import run_with_graph
from nl2sql.llm_registry import (
    LLMRegistry,
    get_usage_summary,
    load_llm_config,
    reset_usage,
)
from nl2sql.engine_factory import make_engine
from nl2sql.settings import settings
from nl2sql.vector_store import SchemaVectorStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NL2SQL LangGraph pipeline.")
    parser.add_argument("--config", type=pathlib.Path, default=pathlib.Path(settings.datasource_config_path))
    parser.add_argument("--id", type=str, default="manufacturing_sqlite", help="Datasource profile id")
    parser.add_argument("--query", type=str, help="User NL query (if omitted, you will be prompted)")
    parser.add_argument("--llm-config", type=pathlib.Path, default=pathlib.Path(settings.llm_config_path), help="Path to LLM config YAML (provider/model per agent)")
    parser.add_argument("--vector-store", type=str, default=settings.vector_store_path, help="Path to vector store directory")
    parser.add_argument("--index", action="store_true", help="Index the schema into the vector store")
    parser.add_argument("--stub-llm", action="store_true", help="Use a stub LLM that returns a fixed plan")
    parser.add_argument("--no-exec", action="store_true", help="Skip execution (generate/validate only)")
    parser.add_argument("--verbose", action="store_true", help="Show raw planner/generator outputs")
    parser.add_argument("--debug", action="store_true", help="Show output of each node in the graph")
    
    # Benchmarking args
    parser.add_argument("--benchmark", action="store_true", help="Run in benchmark mode")
    parser.add_argument("--bench-config", type=pathlib.Path, default=pathlib.Path(settings.benchmark_config_path), help="Path to a single YAML file containing multiple named LLM configs (required if --benchmark is set)")
    parser.add_argument("--iterations", type=int, default=3, help="Number of iterations per config (benchmark mode only)")
    
    return parser.parse_args()


def stub_llm(prompt: str) -> Any:
 
    from nl2sql.schemas import IntentModel, PlanModel, SQLModel, TableRef, OrderSpec
    
    if "[ROLE]\nYou are a SQL Planner" in prompt:
        return PlanModel(
            tables=[TableRef(name="products", alias="p")],
            joins=[],
            filters=[],
            group_by=[],
            aggregates=[],
            having=[],
            order_by=[OrderSpec(expr="p.sku", direction="asc")],
            limit=10,
            select_columns=["p.sku"],
            needed_columns=["p.sku"],
            reasoning="Stub plan"
        )
    # Default to Intent if not Planner/Generator, or check specific Intent keywords
    return IntentModel(
        entities=[],
        filters=[],
        keywords=["users"],
        clarifications=[]
    )


def _render_state(state: Dict[str, Any], registry: LLMRegistry | None = None, verbose: bool = False, usage: Dict[str, int] | None = None) -> None:
    errors: List[str] = state.get("errors") or []
    sql_draft = state.get("sql_draft") or {}
    plan = state.get("plan") or {}
    execution = state.get("execution") or {}

    print("\n=== NL2SQL Run ===")
    print(f"Query: {state.get('user_query')}")

    if errors:
        print("\nErrors:")
        for err in errors:
            print(f" - {err}")

    if sql_draft:
        print("\nSQL:")
        print(sql_draft.get("sql", "").strip())
        if verbose:
            raw_planner = state.get("validation", {}).get("planner_raw")
            if raw_planner:
                print("\nPlanner raw:")
                print(raw_planner)

    if plan:
        print("\nPlan:")
        if plan.get("reasoning"):
            print(f"  Reasoning: {plan['reasoning']}")
        tables = plan.get("tables") or []
        if tables:
            tbl_names = [tbl.get("name") for tbl in tables]
            print(f"  Tables: {', '.join(str(t) for t in tbl_names if t)}")
        order_by = plan.get("order_by") or []
        if order_by:
            ob = order_by[0]
            print(f"  Order By: {ob.get('expr')} {ob.get('direction', 'asc')}")
        print(f"  Limit: {plan.get('limit')}")

    if execution:
        if execution.get("error"):
            print(f"\nExecution Error: {execution['error']}")
        else:
            print(f"\nExecution: {execution.get('row_count', 0)} rows")
            sample = execution.get("sample") or []
            if sample:
                print("Sample:")
                print(_format_table(sample))

    # Performance Table
    print("\nPerformance:")
    latency = state.get("latency") or {}
    usage = usage or {}
    
    # Define nodes and their types
    nodes = [
        ("intent", "AI"),
        ("schema", "Non-AI"),
        ("planner", "AI"),
        ("generator", "Non-AI"),
        ("validator", "Non-AI"),
        ("executor", "Non-AI"),
    ]
    
    rows = []
    total_time = latency.get("total", 0)
    total_tokens = usage.get("_all", {}).get("total_tokens", 0)

    for node, node_type in nodes:
        # Time
        duration = latency.get(node, 0)
        
        # Model
        model = "-"
        if node_type == "AI":
            if registry:
                try:
                    model = registry._agent_cfg(node).model
                except:
                    model = "unknown"
            else:
                model = "stub"
        
        # Tokens
        tokens = 0
        if node_type == "AI":
            # Usage keys are "agent:model"
            for key, val in usage.items():
                if key.startswith(f"{node}:"):
                    tokens += val.get("total_tokens", 0)
        
        rows.append({
            "Node": node.capitalize(),
            "Type": node_type,
            "Model": model,
            "Tokens": tokens if tokens > 0 else "-",
            "Time": f"{duration:.2f}s"
        })
    
    # Add Total row
    rows.append({
        "Node": "TOTAL",
        "Type": "-",
        "Model": "-",
        "Tokens": total_tokens,
        "Time": f"{total_time:.2f}s"
    })
    
    print(_format_table(rows))


def _print_agent_models(registry: LLMRegistry) -> None:
    print("Agent Configuration:")
    for agent in ["intent", "planner"]:
        cfg = registry._agent_cfg(agent)
        print(f"  {agent.capitalize()}: {cfg.model} ({cfg.provider})")


def _format_table(rows: List[Any]) -> str:
    # Simple text table for list of dict rows
    if not rows:
        return ""
    if isinstance(rows[0], dict):
        headers = list(rows[0].keys())
        col_widths = {h: max(len(str(h)), max(len(str(r.get(h, ""))) for r in rows)) for h in headers}
        lines = [" | ".join(h.ljust(col_widths[h]) for h in headers)]
        lines.append("-+-".join("-" * col_widths[h] for h in headers))
        for r in rows:
            lines.append(" | ".join(str(r.get(h, "")).ljust(col_widths[h]) for h in headers))
        return "\n".join(lines)
    # Fallback to repr
    return "\n".join(repr(r) for r in rows)


def main() -> None:
    args = parse_args()
    profiles = load_profiles(args.config)
    profile = get_profile(profiles, args.id)
    engine = make_engine(profile)

    if not args.vector_store:
        print("Error: Vector store path is required (via --vector-store or VECTOR_STORE env var).", file=sys.stderr)
        sys.exit(1)

    vector_store = SchemaVectorStore(persist_directory=args.vector_store)

    if args.index:
        print(f"Indexing schema to {args.vector_store}...")
        vector_store.index_schema(engine)
        if not args.query:
            print("Indexing complete.")
            return

    if vector_store.is_empty():
        print(f"Error: Vector store at '{args.vector_store}' is empty or not initialized.", file=sys.stderr)
        print("Please run indexing first:", file=sys.stderr)
        print(f"  python -m src.nl2sql.cli --index --vector-store {args.vector_store}", file=sys.stderr)
        sys.exit(1)

    query = args.query
    if not query:
        query = input("Enter your question: ").strip()
    
    if not query:
        print("No query provided.", file=sys.stderr)
        sys.exit(1)

    if args.benchmark:
        if not args.bench_config:
            print("Error: --bench-config is required for benchmark mode.", file=sys.stderr)
            sys.exit(1)
            
        if not args.bench_config.exists():
            print(f"Error: Benchmark config file not found: {args.bench_config}", file=sys.stderr)
            sys.exit(1)

        import yaml
        from nl2sql.llm_registry import parse_llm_config
        
        try:
            bench_data = yaml.safe_load(args.bench_config.read_text()) or {}
        except Exception as e:
            print(f"Error reading benchmark config: {e}", file=sys.stderr)
            sys.exit(1)
            
        if not isinstance(bench_data, dict):
            print("Error: Benchmark config must be a dictionary of named configurations.", file=sys.stderr)
            sys.exit(1)

        print(f"Starting benchmark for query: '{query}'")
        results = []
        
        for name, cfg_data in bench_data.items():
            print(f"\n--- Benchmarking Config: {name} ---")
            try:
                # Validate it looks like a config
                if not isinstance(cfg_data, dict):
                    print(f"Skipping {name}: invalid format (expected dict)")
                    continue
                    
                llm_cfg = parse_llm_config(cfg_data)
            except Exception as e:
                print(f"Failed to parse config {name}: {e}")
                continue

            registry = LLMRegistry(llm_cfg, engine=engine, row_limit=profile.row_limit)
            _print_agent_models(registry)
            llm_map = registry.llm_map()

            latencies = []
            success_count = 0
            total_tokens = 0
            
            for i in range(args.iterations):
                print(f"  Iteration {i+1}/{args.iterations}...", end="", flush=True)
                reset_usage()
                
                try:
                    state = run_with_graph(
                        profile, 
                        query, 
                        llm_map=llm_map, 
                        execute=True, 
                        vector_store=vector_store
                    )
                    
                    latency = state.get("latency", {}).get("total", 0)
                    latencies.append(latency)
                    
                    # Check success: SQL generated and no execution errors
                    if state.get("sql_draft") and not state.get("execution", {}).get("error") and not state.get("errors"):
                        success_count += 1
                        print(f" Success ({latency:.2f}s)")
                    else:
                        errs = state.get("errors", [])
                        exec_err = state.get("execution", {}).get("error")
                        if exec_err: errs.append(f"Exec: {exec_err}")
                        print(f" Failed ({latency:.2f}s) - {'; '.join(errs)}")
                    
                    usage = get_usage_summary()
                    total_tokens += usage.get("_all", {}).get("total_tokens", 0)
                    
                except Exception as e:
                    print(f" Error: {e}")
            
            avg_latency = statistics.mean(latencies) if latencies else 0
            avg_tokens = total_tokens / args.iterations if args.iterations > 0 else 0
            success_rate = (success_count / args.iterations) * 100
            
            results.append({
                "config": name,
                "avg_latency": avg_latency,
                "success_rate": success_rate,
                "avg_tokens": avg_tokens
            })

        print("\n\n=== Benchmark Results ===")
        print(f"{'Config':<25} | {'Success':<8} | {'Avg Latency':<12} | {'Avg Tokens':<10}")
        print("-" * 65)
        for res in results:
            print(f"{res['config']:<25} | {res['success_rate']:>6.1f}% | {res['avg_latency']:>10.2f}s | {res['avg_tokens']:>10.1f}")
        return

    # Normal execution flow
    llm_map = None
    llm = None
    registry = None
    if args.stub_llm:
        llm_map = {
            "intent": stub_llm,
            "planner": stub_llm,
            "executor": stub_llm,
            "_default": stub_llm,
        }
    else:
        if not args.llm_config:
            print("LLM config is required unless using --stub-llm", file=sys.stderr)
            sys.exit(1)
        llm_cfg = load_llm_config(args.llm_config)
        registry = LLMRegistry(llm_cfg, engine=engine, row_limit=profile.row_limit)
        _print_agent_models(registry)
        llm_map = registry.llm_map()

    reset_usage()
    state = run_with_graph(profile, query, llm=llm, llm_map=llm_map, execute=not args.no_exec, vector_store=vector_store, debug=args.debug)
    usage = get_usage_summary()
    _render_state(state, registry=registry, verbose=args.verbose, usage=usage)


if __name__ == "__main__":
    main()
