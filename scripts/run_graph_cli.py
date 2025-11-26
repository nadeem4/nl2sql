#!/usr/bin/env python3
"""
Run the LangGraph NL→SQL pipeline with an optional stub LLM.

Example:
  python3 scripts/run_graph_cli.py --query "list products"
  python3 scripts/run_graph_cli.py --query "show defects" --stub-llm
"""
import argparse
import pathlib
import sys
from pprint import pprint

# Allow running without packaging
ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.append(str(SRC))

from datasource_config import get_profile, load_profiles
from langgraph_pipeline import run_with_graph
from llm_registry import load_llm_map


def stub_llm(prompt: str) -> str:
    """
    Simple stub that returns a products listing plan JSON.
    """
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the LangGraph NL→SQL pipeline.")
    parser.add_argument("--config", type=pathlib.Path, default=ROOT / "configs" / "datasources.example.yaml")
    parser.add_argument("--id", type=str, default="manufacturing_sqlite", help="Datasource profile id")
    parser.add_argument("--query", type=str, required=True, help="User NL query")
    parser.add_argument("--stub-llm", action="store_true", help="Use a stub LLM that returns a fixed plan")
    parser.add_argument("--llm-config", type=pathlib.Path, help="Path to LLM config YAML (provider/model per agent)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profiles = load_profiles(args.config)
    profile = get_profile(profiles, args.id)
    llm = stub_llm if args.stub_llm else None
    llm_map = load_llm_map(args.llm_config) if args.llm_config else None

    state = run_with_graph(profile, args.query, llm=llm, llm_map=llm_map)
    pprint(state)


if __name__ == "__main__":
    main()
