"""The error a stub raises until the row that fills it lands (docs/architecture/wave_c.md, row C0)."""
from __future__ import annotations


def arrives_with(what: str, row: str) -> NotImplementedError:
    """``NotImplementedError`` saying ``what`` arrives with PR ``row`` of Wave C's plan; ``raise`` it."""
    return NotImplementedError(f"{what} arrives with {row} (docs/architecture/wave_c.md, row {row}).")
