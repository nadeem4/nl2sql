"""Datasets vendored in the wheel, shared by the demo and evaluation.

``chinook.sqlite`` is the Chinook sample database (MIT; see
``THIRD_PARTY_NOTICES.md``). ``nl2sql demo`` copies it into the demo project,
and the gold evaluation dataset reads it directly.
"""
from pathlib import Path

CHINOOK_DB_PATH = Path(__file__).resolve().parent / "chinook.sqlite"

__all__ = ["CHINOOK_DB_PATH"]
