#!/usr/bin/env python3
"""Fail if the published packages do not all declare the same version.

This monorepo uses unified versioning: every distribution is released together
under one version number, and the internal dependencies are expressed as
compatible-release constraints against that line. If the declared versions
drift apart, no consistent set of wheels satisfies them all and users get an
unresolvable install.

Run from anywhere:

    python scripts/check_versions.py

Needs Python 3.11+ for ``tomllib``. CI runs it on the 3.11 build job.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

# The nine distributions built and uploaded by publish_pypi.yaml. Keep in sync.
PACKAGES = (
    "packages/adapter-sdk",
    "packages/adapter-sqlalchemy",
    "packages/core",
    "packages/cli",
    "packages/api",
    "packages/adapters/mssql",
    "packages/adapters/mysql",
    "packages/adapters/postgres",
    "packages/adapters/sqlite",
)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent

    declared: list[tuple[str, str, str]] = []
    for package in PACKAGES:
        pyproject = repo_root / package / "pyproject.toml"
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        declared.append((project["version"], project["name"], package))

    versions = {version for version, _, _ in declared}
    if len(versions) == 1:
        print(f"OK: all {len(declared)} packages declare version {versions.pop()}.")
        return 0

    print("Version drift: every package must declare the same version.")
    for version, name, package in sorted(declared):
        print(f"  {version:<12} {name} ({package}/pyproject.toml)")
    print(f"\nFound {len(versions)} distinct versions: {', '.join(sorted(versions))}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
