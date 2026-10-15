"""Policy patterns: ``*``, ``datasource`` or ``datasource.schema.table``, each part an ``fnmatch`` glob.

This module is the only place a policy pattern is parsed or matched (the
config only asks it whether a pattern is well formed). Matching ignores case,
so a deny cannot be dodged by ``Orders`` against ``orders``.
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Iterable, List, Mapping, Optional, Set, Tuple

SHAPES = "'*', 'datasource' or 'datasource.schema.table'"


@dataclass(frozen=True)
class Pattern:
    """One parsed pattern. ``schema`` and ``table`` are ``None`` for a whole datasource."""

    text: str
    datasource: str
    schema: Optional[str] = None
    table: Optional[str] = None

    def covers_datasource(self, datasource_id: str) -> bool:
        """Whether this pattern can match anything inside ``datasource_id``."""
        return fnmatchcase(datasource_id.lower(), self.datasource)

    def covers_all_of(self, datasource_id: str) -> bool:
        """Whether this pattern matches every table in ``datasource_id``."""
        whole = self.schema is None or (self.schema == "*" and self.table == "*")
        return whole and self.covers_datasource(datasource_id)

    def matches(self, datasource_id: str, schema: str, table: str) -> bool:
        if not self.covers_datasource(datasource_id):
            return False
        if self.schema is None:
            return True
        return fnmatchcase(schema.lower(), self.schema) and fnmatchcase(table.lower(), self.table)


def matching_datasources(text: str, datasource_ids: Iterable[str]) -> List[str]:
    """The ids in ``datasource_ids`` the pattern ``text`` can match inside, sorted."""
    pattern = parse_pattern(text)
    return sorted(ds for ds in datasource_ids if pattern.covers_datasource(ds))


def patterns_matching(roles: Mapping[str, Any], datasource_id: str,
                      tables: Iterable[Tuple[str, str]]) -> Set[Tuple[str, str, str]]:
    """Each role's pattern that matches one of ``tables`` (``(schema, table)``) in ``datasource_id``.

    ``roles`` is ``policies.roles``: each role has ``allow`` and ``deny``
    pattern lists. Returns ``(role, "allow" or "deny", pattern)``. Schema
    drift's impact asks it which rules matched a dropped table and match
    nothing left (``nl2sql/schema/impact.py``), so pattern matching stays here.
    """
    tables = list(tables)
    found: Set[Tuple[str, str, str]] = set()
    for name, role in roles.items():
        for kind in ("allow", "deny"):
            for text in getattr(role, kind):
                pattern = parse_pattern(text)
                if any(pattern.matches(datasource_id, schema, table) for schema, table in tables):
                    found.add((name, kind, text))
    return found


def parse_pattern(text: str) -> Pattern:
    """The pattern ``text`` stands for.

    Raises:
        ValueError: ``text`` is not one of the shapes. A two-part pattern is
            rejected with the fix, since ``sales.orders`` could mean a table in
            any schema.
    """
    if text == "*":
        return Pattern(text, "*")
    parts = text.split(".")
    if len(parts) == 2 and all(parts):
        datasource, table = parts
        if table == "*":
            fix = f"write '{datasource}' for the whole datasource, or '{datasource}.<schema>.*'"
        else:
            fix = f"write '{datasource}.<schema>.{table}', or '{datasource}.<schema>.*'"
        raise ValueError(f"'{text}' has two parts: {fix}.")
    if len(parts) not in (1, 3) or not all(parts):
        raise ValueError(f"Invalid pattern '{text}'. Use {SHAPES}; each part may be a glob.")
    lowered = [p.lower() for p in parts]
    if len(lowered) == 1:
        return Pattern(text, lowered[0])
    return Pattern(text, *lowered)
