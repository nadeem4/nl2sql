"""The error a stub raises until the row that fills it lands."""
from __future__ import annotations


def arrives_with(what: str, row: str) -> NotImplementedError:
    """``NotImplementedError`` saying ``what`` arrives with PR ``row`` of plan; ``raise`` it."""
    return NotImplementedError(f"{what} arrives with {row}.")
