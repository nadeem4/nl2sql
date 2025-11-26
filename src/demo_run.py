#!/usr/bin/env python3
"""
Simple demo runner that:
- Loads a datasource profile from YAML
- Creates a SQLAlchemy engine (SQLite by default)
- Runs a read-only query with a row limit
"""
import argparse
import pathlib
import sys
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

# Allow running as a script without packaging
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from src.datasource_config import get_profile, load_profiles
from src.engine_factory import make_engine, run_read_query


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a read-only query against a datasource profile.")
    parser.add_argument("--config", type=pathlib.Path, default=pathlib.Path("configs/datasources.example.yaml"))
    parser.add_argument("--id", type=str, default="manufacturing_sqlite", help="Datasource profile id")
    parser.add_argument(
        "--sql",
        type=str,
        default="SELECT sku, name, category FROM products ORDER BY sku",
        help="SQL query to execute (LIMIT is enforced)",
    )
    parser.add_argument("--limit", type=int, default=20, help="Max rows to return")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profiles = load_profiles(args.config)
    profile = get_profile(profiles, args.id)

    try:
        engine = make_engine(profile)
    except Exception as exc:
        print(f"Failed to create engine for profile '{profile.id}': {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        rows = run_read_query(engine, args.sql, row_limit=min(args.limit, profile.row_limit))
    except SQLAlchemyError as exc:
        print(f"Query failed: {exc}", file=sys.stderr)
        sys.exit(2)

    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
