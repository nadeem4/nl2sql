"""Print fresh random passwords for the test bed's three servers, one ``NAME=value`` line each.

    python testbed/passwords.py > testbed/.env

The compose file has no default password: each one is required, so no
password is ever written in the repository. Docker Compose reads
``testbed/.env`` (git-ignored) by itself; the ``realdb`` tests read the same
variables from the environment. CI makes new ones for every run.

Each value is URL-safe and always holds an upper-case letter, a lower-case
letter, a digit and a hyphen, so it passes SQL Server's password policy.
"""
from __future__ import annotations

import secrets
import sys
from typing import List, Optional

NAMES = ("TESTBED_POSTGRES_PASSWORD", "TESTBED_MYSQL_PASSWORD", "TESTBED_MSSQL_PASSWORD")


def generate() -> str:
    return "Tb1-" + secrets.token_urlsafe(24)


def main(argv: Optional[List[str]] = None) -> int:
    for name in NAMES:
        print(f"{name}={generate()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
