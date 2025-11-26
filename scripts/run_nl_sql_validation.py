#!/usr/bin/env python3
"""
CLI wrapper to run NL→SQL goldens against the SQLite manufacturing DB.
"""
import argparse
import pathlib
import sys

# Allow running without packaging
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from tests.nl_to_sql_validation import main as run_validation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run NL→SQL validation goldens.")
    return parser.parse_args()


def main() -> None:
    parse_args()  # reserved for future flags
    run_validation()


if __name__ == "__main__":
    main()
