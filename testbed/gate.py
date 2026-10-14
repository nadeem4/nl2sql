"""The ``realdb-gate`` job's decision: the required check of the ``realdb`` workflow.

    python testbed/gate.py --changes <result> --realdb <result>

Each result is a GitHub Actions job result: ``success``, ``failure``,
``cancelled`` or ``skipped``. The gate passes when ``changes`` succeeded and
``realdb`` either passed or was skipped because nothing database-facing
changed. Anything else fails it, so a skipped run is an honest pass and a run
that never happened is never mistaken for one that did.
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Tuple


def verdict(changes: str, realdb: str) -> Tuple[bool, str]:
    if changes != "success":
        return False, f"the changes job ended {changes}, so whether realdb had to run is unknown (realdb: {realdb})"
    if realdb == "success":
        return True, "realdb: success"
    if realdb == "skipped":
        return True, "realdb: skipped, because nothing database-facing changed"
    return False, f"realdb: {realdb}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="The realdb workflow's required check.")
    parser.add_argument("--changes", required=True)
    parser.add_argument("--realdb", required=True)
    args = parser.parse_args(argv)
    passed, why = verdict(args.changes, args.realdb)
    print(("pass: " if passed else "fail: ") + why)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
