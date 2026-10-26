"""Join candidates: pairs of columns that look like a key and what refers to it (docs/architecture/wave_b.md, section 6).

For each column ``A.c`` that is not part of a foreign key, the profiler looks for a single-column primary key
``B.k`` (or the only column named ``id``) it may point to:

1. **Names.** ``c`` equals ``k`` (not when ``k`` is a bare ``id``, which every table has), or ``c`` is ``B``'s
   name, singular or plural, followed by ``id``, ``_id``, ``key`` or ``_key``; case and underscores ignored.
2. **Types.** Both columns fall in the same category (number, text, temporal, boolean); ``other`` never matches.

The overlap probe (step 3) and the budget are in ``run``. A pair with a declared foreign key between its tables
is never a candidate, nor is one the glossary already has (``known``), nor one on a table ``allows`` refuses.
Two primary keys of one name (``film.film_id``, ``film_text.film_id``) are one ``one-to-one`` pair, in the order
of the tables' full names, not two. The keys are indexed by name once, so a wide schema costs one dictionary
lookup per column.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Dict, FrozenSet, List, Optional, Set, Tuple

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
    cardinality: str  # many-to-one when the key is a primary key, one-to-one for two of them, else unknown


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


def find_candidates(snapshot: SchemaSnapshot, *, dialect: Optional[str], allows: Allows,
                    known: Known = frozenset()) -> List[JoinCandidate]:
    """Every name-and-type join candidate between the tables ``allows`` reaches, exact key-name matches first.

    The order is otherwise the snapshot's, so a run is repeatable.
    """
    from nl2sql.aggregation.join_keys import OTHER
    from nl2sql.pipeline.nodes.validator.join_keys import column_category

    categories: Dict[str, str] = {}

    def category(data_type: str) -> str:
        if data_type not in categories:
            categories[data_type] = column_category(data_type, dialect)
        return categories[data_type]

    tables = [t for t in snapshot.contract.tables.values() if allows(t.table.schema_name, t.table.table_name)]
    # The keys by name: `by_key` for a column named like the key, `by_form` for table name + id or key.
    by_key: Dict[str, List[Tuple[TableContract, ColumnContract]]] = {}
    by_form: Dict[str, List[Tuple[TableContract, ColumnContract]]] = {}
    for table in tables:
        key = _key_of(table)
        if key is None:
            continue
        if squash(key.name) != "id":
            by_key.setdefault(squash(key.name), []).append((table, key))
        for form in _forms(table.table.table_name):
            for suffix in KEY_SUFFIXES:
                by_form.setdefault(form + suffix, []).append((table, key))
    linked = {frozenset({t.table.full_name, fk.referred_table.full_name})
              for t in snapshot.contract.tables.values() for fk in t.foreign_keys}

    found: Dict[FrozenSet[Tuple[str, str]], JoinCandidate] = {}
    for left in tables:
        in_a_key = {c.lower() for fk in left.foreign_keys for c in fk.constrained_columns}
        for column in left.columns.values():
            if column.name.lower() in in_a_key:
                continue
            squashed = squash(column.name)
            matches = [(SAME_NAME, m) for m in by_key.get(squashed, [])]
            matches += [(TABLE_KEY, m) for m in by_form.get(squashed, [])]
            for rule, (right, key) in matches:
                if right is left or frozenset({left.table.full_name, right.table.full_name}) in linked:
                    continue
                kind = category(column.data_type)
                if kind == OTHER or kind != category(key.data_type):
                    continue
                pair = frozenset({(left.table.full_name, column.name.lower()),
                                  (right.table.full_name, key.name.lower())})
                if pair in known:
                    continue
                candidate = JoinCandidate(left, column, right, key, rule, kind,
                                          "many-to-one" if key.is_primary_key else "unknown")
                earlier = found.get(pair)
                if earlier is None:
                    found[pair] = candidate
                    continue
                # The same two columns from both sides (and, for a table-name match, perhaps both rules).
                both_keys = earlier.left_column.is_primary_key and earlier.right_column.is_primary_key
                first = min(earlier, candidate, key=lambda c: c.left.table.full_name)
                found[pair] = replace(first, rule=SAME_NAME if SAME_NAME in (earlier.rule, rule) else first.rule,
                                      cardinality="one-to-one" if both_keys else first.cardinality)
    return sorted(found.values(), key=lambda c: c.rule != SAME_NAME)  # stable: exact key-name matches first
