"""``ConfigError``: every problem in a config, reported at once."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

MAPPING_PAGE = "architecture/configuration_v2"


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a config.

    ``file`` is the file the problem is in, as written in ``nl2sql.yaml``
    (``nl2sql.yaml`` itself for an inline section). ``path`` is where inside it
    (``a.b[2].c``), empty for the file as a whole. ``message`` says what is
    wrong in one sentence. ``fix`` is how to fix it, when that is known.
    """

    file: str
    path: str
    message: str
    fix: Optional[str] = None

    def __str__(self) -> str:
        where = f"{self.file} → {self.path}" if self.path else self.file
        lines = [f"  {where}", f"    {self.message}"]
        if self.fix:
            lines.append(f"    Fix: {self.fix}")
        return "\n".join(lines)


class ConfigError(ValueError):
    """The config has problems. ``problems`` lists every one of them."""

    def __init__(self, problems: Sequence[Problem], source: str = "nl2sql.yaml"):
        self.problems: List[Problem] = list(problems)
        self.source = source
        super().__init__(self._render())

    def _render(self) -> str:
        count = len(self.problems)
        noun = "problem" if count == 1 else "problems"
        body = "\n\n".join(str(p) for p in self.problems)
        return f"{self.source} has {count} {noun}:\n\n{body}"
