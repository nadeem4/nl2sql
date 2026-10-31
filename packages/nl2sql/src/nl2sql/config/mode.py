"""``resolve_mode``: which mode a process runs in (docs/architecture/wave_d.md, D229)."""
from __future__ import annotations

from typing import Optional, get_args

from nl2sql.config.models import Mode

MODES = get_args(Mode)


def resolve_mode(flag: Optional[str], env: Optional[str], file: str) -> str:
    """The mode: the launcher ``flag`` if given, else the ``NL2SQL_MODE`` value ``env``, else the config's ``file``.

    Pure: the entry point reads the environment and passes the value in; this reads none. An empty ``env`` counts
    as unset.

    Raises:
        ValueError: the winning value is not one of ``demo``, ``your_data`` or ``hosted``.
    """
    value = flag or env or file
    if value not in MODES:
        raise ValueError(f"Unknown mode '{value}'. Use one of: {', '.join(MODES)}.")
    return value
