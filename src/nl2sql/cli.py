#!/usr/bin/env python3
"""
CLI entrypoint for the NL2SQL LangGraph pipeline.
"""
import argparse
import pathlib
import sys
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
    return parser.parse_args()


def stub_llm(prompt: str) -> str:
    return """
    {
      "tables": [{"name": "products", "alias": "p"}],
      "joins": [],
      "filters": [],
      "group_by": [],
      "aggregates": [],
      "having": [],
      "order_by": [{"expr": "p.sku", "direction": "asc"}],
      "limit": 10
    }
    """


def _render_state(state: Dict[str, Any], verbose: bool = False, usage: Dict[str, int] | None = None) -> None:
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
    if usage:
        print("\nLLM Usage:")
        for key, val in usage.items():
            label = "All" if key == "_all" else key
            print(f"  {label}: prompt={val.get('prompt_tokens', 0)}, completion={val.get('completion_tokens', 0)}, total={val.get('total_tokens', 0)}")


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

    llm_map = None
    llm = None
    engine = make_engine(profile)
    if args.stub_llm:
        llm_map = {
            "intent": stub_llm,
            "planner": stub_llm,
            "generator": stub_llm,
            "executor": stub_llm,
            "_default": stub_llm,
        }
    else:
        if not args.llm_config:
            print("LLM config is required unless using --stub-llm", file=sys.stderr)
            sys.exit(1)
        llm_cfg = load_llm_config(args.llm_config)
        registry = LLMRegistry(llm_cfg, engine=engine, row_limit=profile.row_limit)
        llm_map = registry.llm_map()
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

    reset_usage()
    state = run_with_graph(profile, query, llm=llm, llm_map=llm_map, execute=not args.no_exec, vector_store=vector_store, debug=args.debug)
    usage = get_usage_summary()
    _render_state(state, verbose=args.verbose, usage=usage)


if __name__ == "__main__":
    main()
