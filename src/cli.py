#!/usr/bin/env python3
"""
CLI entrypoint for the NL2SQL LangGraph pipeline.
"""
import argparse
import pathlib
import sys
from typing import Any, Dict, List

from datasource_config import get_profile, load_profiles
from langgraph_pipeline import run_with_graph
from llm_registry import load_llm_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NL2SQL LangGraph pipeline.")
    parser.add_argument("--config", type=pathlib.Path, default=pathlib.Path("configs/datasources.example.yaml"))
    parser.add_argument("--id", type=str, default="manufacturing_sqlite", help="Datasource profile id")
    parser.add_argument("--query", type=str, help="User NL query (if omitted, you will be prompted)")
    parser.add_argument("--llm-config", type=pathlib.Path, help="Path to LLM config YAML (provider/model per agent)")
    parser.add_argument("--stub-llm", action="store_true", help="Use a stub LLM that returns a fixed plan")
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


def _render_state(state: Dict[str, Any]) -> None:
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

    llm = stub_llm if args.stub_llm else None
    llm_map = load_llm_map(args.llm_config) if args.llm_config else None

    query = args.query or input("Enter your question: ").strip()
    if not query:
        print("No query provided.", file=sys.stderr)
        sys.exit(1)

    state = run_with_graph(profile, query, llm=llm, llm_map=llm_map)
    _render_state(state)


if __name__ == "__main__":
    main()
