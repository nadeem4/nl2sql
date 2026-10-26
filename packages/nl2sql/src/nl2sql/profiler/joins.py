"""Join candidates: pairs of columns that look like a key and what refers to it (docs/architecture/wave_b.md, section 6).

For each column ``A.c`` that is not part of a foreign key, the profiler looks for a single-column primary key
``B.k`` (or the only column named ``id``) it may point to:

1. **Names.** ``c`` equals ``k`` (not when ``k`` is a bare ``id``, which every table has), or ``c`` is ``B``'s
   name, singular or plural, followed by ``id``, ``_id``, ``key`` or ``_key``; case and underscores ignored.
2. **Types.** Both columns fall in the same category (number, text, temporal, boolean); ``other`` never matches.

The overlap probe (step 3) and the budget are in ``run``. A pair with a declared foreign key between its tables
is never a candidate, nor is one the glossary already has (``known``), nor one on a table ``allows`` refuses.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, FrozenSet, List, Optional, Set, Tuple

from nl2sql_sdk.schema import ColumnContract, SchemaSnapshot, TableContract

Allows = Callable[[Optional[str], str], bool]
Known = FrozenSet[FrozenSet[Tuple[str, str]]]
"""Column pairs already joined in the glossary, each side as ``(table's full name, lower-case column)``."""

SAME_NAME = "same_name"
TABLE_KEY = "table_name_key"
KEY_SUFFIXES = ("id", "key")


@dataclass(frozen=True)
class JoinCandidate:
    left: TableContract
    left_column: ColumnContract
    right: TableContract
    right_column: ColumnContract
    rule: str  # SAME_NAME or TABLE_KEY: the name rule that matched
    category: str  # the type category both columns share
    cardinality: str  # many-to-one when the key is a primary key, else unknown


def squash(name: str) -> str:
    """``name`` lower-cased with underscores removed: ``customer_id`` and ``CustomerId`` compare equal."""
    return name.lower().replace("_", "")


def _forms(table_name: str) -> Set[str]:
    """The table's squashed name, singular and plural."""
    name = squash(table_name)
    forms = {name}
    if name.endswith("ies"):
        forms.add(name[:-3] + "y")
    elif name.endswith("s"):
        forms.add(name[:-1])
    if name.endswith("y"):
        forms.add(name[:-1] + "ies")
    forms.add(name + "s")
    return forms


def _key_of(table: TableContract) -> Optional[ColumnContract]:
    """The table's single-column primary key, else the only column named ``id``."""
    keys = [c for c in table.columns.values() if c.is_primary_key]
    if len(keys) == 1:
        return keys[0]
    if not keys:
        ids = [c for c in table.columns.values() if c.name.lower() == "id"]
        if len(ids) == 1:
            return ids[0]
    return None


def _name_rule(column: str, key: str, table_name: str) -> Optional[str]:
    c, k = squash(column), squash(key)
    if c == k and k != "id":
        return SAME_NAME
    if any(c == form + suffix for form in _forms(table_name) for suffix in KEY_SUFFIXES):
        return TABLE_KEY
    return None


def find_candidates(snapshot: SchemaSnapshot, *, dialect: Optional[str], allows: Allows,
                    known: Known = frozenset()) -> List[JoinCandidate]:
    """Every name-and-type join candidate between the tables ``allows`` reaches, exact key-name matches first.

    The order is otherwise the snapshot's, so a run is repeatable.
    """
    from nl2sql.pipeline.nodes.validator.join_keys import column_category

    from nl2sql.aggregation.join_keys import OTHER

    tables = [t for t in snapshot.contract.tables.values() if allows(t.table.schema_name, t.table.table_name)]
    keys = {t.table.full_name: _key_of(t) for t in tables}
    linked = {frozenset({t.table.full_name, fk.referred_table.full_name})
              for t in snapshot.contract.tables.values() for fk in t.foreign_keys}
    found: List[JoinCandidate] = []
    for left in tables:
        in_a_key = {c.lower() for fk in left.foreign_keys for c in fk.constrained_columns}
        for column in left.columns.values():
            if column.name.lower() in in_a_key:
                continue
            for right in tables:
                key = keys[right.table.full_name]
                if right is left or key is None or frozenset({left.table.full_name, right.table.full_name}) in linked:
                    continue
                rule = _name_rule(column.name, key.name, right.table.table_name)
                if rule is None:
                    continue
                category = column_category(column.data_type, dialect)
                if category == OTHER or category != column_category(key.data_type, dialect):
                    continue
                pair = frozenset({(left.table.full_name, column.name.lower()),
                                  (right.table.full_name, key.name.lower())})
                if pair in known:
                    continue
                found.append(JoinCandidate(left, column, right, key, rule, category,
                                           "many-to-one" if key.is_primary_key else "unknown"))
    return sorted(found, key=lambda c: c.rule != SAME_NAME)  # stable: the exact key-name matches first
