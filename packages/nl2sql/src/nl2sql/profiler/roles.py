"""Proposed column roles: heuristics a person should confirm (docs/architecture/wave_b.md, section 6, D61).

What the snapshot proves is *derived* (a primary or foreign key is an ``identifier``, a temporal type a
``date``) and never proposed. The rules below are the proposed ones, one row each, from the snapshot alone:

| Proposed | When |
|---|---|
| ``money`` | a decimal column named like ``price``, ``amount``, ``total``, ``cost``, ``revenue``, ``salary``, ``fee``, ``balance``; ``unit`` from a ``_usd``, ``_eur`` or ``_gbp`` suffix |
| ``quantity`` | an integer or decimal column named like ``qty``, ``quantity``, ``count``, ``units``, or with a ``_kg``, ``_ms`` or ``_pct`` suffix (that suffix is the ``unit``) |
| ``enum`` | a text or integer column, not an identifier (nor named like a key, ``..._id``), with at most ``enum_max_distinct`` distinct values; ``values`` only when the snapshot holds all of them |
| ``date`` on text | text whose smallest and largest values are ISO dates |
| a refined ``grain`` | ``month`` or ``year`` when every value of a date column is the first of a month or of a year |

The word lists are English only. A role, unit, grain or set of values the glossary already has is not proposed
again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from nl2sql_sdk.knowledge import ColumnKnowledge
from nl2sql_sdk.schema import ColumnStatistics, ColumnContract, SchemaSnapshot, TableContract

Allows = Callable[[Optional[str], str], bool]
Said = Dict[Tuple[str, str], ColumnKnowledge]
"""What the glossary says about a column, keyed ``(table's full name, lower-case column)``."""

MONEY_WORDS = {"price", "amount", "total", "cost", "revenue", "salary", "fee", "balance"}
QUANTITY_WORDS = {"qty", "quantity", "count", "units"}
CURRENCY_SUFFIXES = {"usd": "USD", "eur": "EUR", "gbp": "GBP"}
UNIT_SUFFIXES = ("kg", "ms", "pct")

_TOKEN = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}):(\d{2})(?:\.0+)?)?(?:Z|[+-]00:?00)?$")


@dataclass(frozen=True)
class RoleFinding:
    table: TableContract
    column: ColumnContract
    change: Dict[str, Any]  # the fields it would set, in the glossary's shape
    evidence: Dict[str, Any] = field(default_factory=dict)  # counts and checks; never rows


def _tokens(name: str) -> List[str]:
    return [t.lower() for t in _TOKEN.findall(name.replace("_", " ").replace("-", " "))]


def _has_word(tokens: List[str], words: Set[str]) -> bool:
    return any(t in words or t.rstrip("s") in words for t in tokens)


def _kind(data_type: str, dialect: Optional[str]) -> str:
    """``integer``, ``decimal``, ``text``, ``temporal`` or ``other`` for a database type name."""
    from sqlglot import expressions as exp
    from sqlglot.errors import SqlglotError

    try:
        built = exp.DataType.build(data_type, dialect=dialect)
    except (SqlglotError, ValueError):
        return "other"
    if built.is_type(*exp.DataType.INTEGER_TYPES):
        return "integer"
    if built.is_type(*exp.DataType.REAL_TYPES):
        return "decimal"
    if built.is_type(*exp.DataType.TEXT_TYPES):
        return "text"
    if built.is_type(*exp.DataType.TEMPORAL_TYPES):
        return "temporal"
    return "other"


def _named_role(name: str, kind: str) -> Optional[Dict[str, Any]]:
    """``money`` or ``quantity`` from the column's name (and its unit suffix), else None."""
    tokens = _tokens(name)
    suffix = tokens[-1] if len(tokens) > 1 else None
    if kind == "decimal" and (_has_word(tokens, MONEY_WORDS) or suffix in CURRENCY_SUFFIXES):
        return {"role": "money", **({"unit": CURRENCY_SUFFIXES[suffix]} if suffix in CURRENCY_SUFFIXES else {})}
    if kind in ("integer", "decimal") and suffix in UNIT_SUFFIXES:
        return {"role": "quantity", "unit": suffix}
    if kind in ("integer", "decimal") and _has_word(tokens, QUANTITY_WORDS):
        return {"role": "quantity"}
    return None


def _first_of(value: Any) -> Optional[str]:
    """``year`` or ``month`` for midnight on the first of a year or month, ``day`` for any other date, else None."""
    match = _DATE.match(value) if isinstance(value, str) else None
    if match is None:
        return None
    _, month, day, hour, minute, second = match.groups()
    if (hour, minute, second) not in ((None, None, None), ("00", "00", "00")):
        return "day"
    if day != "01":
        return "day"
    return "year" if month == "01" else "month"


def _grain(stats: ColumnStatistics) -> Optional[str]:
    """``month`` or ``year`` when the snapshot holds every distinct value and each is the first of one."""
    values = stats.sample_values
    if not values or len(values) < stats.distinct_count:
        return None
    grains = [_first_of(v) for v in values]
    if None in grains or "day" in grains:
        return None
    return "month" if "month" in grains else "year"


def _change(column: ColumnContract, kind: str, stats: Optional[ColumnStatistics], enum_max_distinct: int
            ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The fields the heuristics would set on a column that is not an identifier, and their evidence."""
    change: Dict[str, Any] = {}
    evidence: Dict[str, Any] = {"type": column.data_type}
    if kind == "temporal":  # the role is derived; only a refined grain is new
        grain = _grain(stats) if stats else None
        if grain:
            change["grain"] = grain
    elif kind == "text" and stats and _first_of(stats.min_value) and _first_of(stats.max_value):
        change["role"] = "date"
        grain = _grain(stats)
        if grain:
            change["grain"] = grain
    elif (named := _named_role(column.name, kind)) is not None:
        change.update(named)
        evidence["rule"] = "name"
    elif (kind in ("text", "integer") and stats and 0 < stats.distinct_count <= enum_max_distinct
          and _tokens(column.name)[-1:] not in (["id"], ["key"])):  # an undeclared key is not a code list
        change["role"] = "enum"
        evidence["distinct"] = stats.distinct_count
        if len(stats.sample_values) == stats.distinct_count:
            change["values"] = {str(v): "" for v in stats.sample_values}
    return change, evidence


def find_roles(snapshot: SchemaSnapshot, *, dialect: Optional[str], allows: Allows, said: Said,
               enum_max_distinct: int) -> List[RoleFinding]:
    """The proposed roles for every column of every table ``allows`` reaches, in the snapshot's order."""
    found: List[RoleFinding] = []
    for key, table in snapshot.contract.tables.items():
        if not allows(table.table.schema_name, table.table.table_name):
            continue
        metadata = snapshot.metadata.tables.get(key)
        in_a_key = {c.lower() for fk in table.foreign_keys for c in fk.constrained_columns}
        for column in table.columns.values():
            if column.is_primary_key or column.name.lower() in in_a_key:
                continue  # an identifier: derived
            meta = metadata.columns.get(column.name) if metadata else None
            change, evidence = _change(column, _kind(column.data_type, dialect), meta.statistics if meta else None,
                                       enum_max_distinct)
            say = said.get((table.table.full_name, column.name.lower()))
            if say is not None:
                if say.role:
                    change.pop("role", None)
                    change.pop("values", None)  # values go with an enum role
                for name in ("unit", "grain", "values"):
                    if getattr(say, name):
                        change.pop(name, None)
            if change:
                found.append(RoleFinding(table, column, change, evidence))
    return found
