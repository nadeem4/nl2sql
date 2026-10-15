"""Glossary names against the live snapshot, the roles the snapshot proves, and the problems (wave_b.md, section 3).

A table is named as ``preferred_tables`` names it: the bare table name or
``schema.table``, case ignored. A column is ``table.column`` or
``schema.table.column``. Names are resolved when the knowledge is used, not
when it is loaded, since a glossary is written before (or without) an index:

- a name that resolves to one table or column is used;
- one that resolves to nothing, or a bare name matching tables in two
  schemas, is not used and is a *problem*, never an error in a run;
- a join whose two sides' types cannot be compared (Wave A D30: a date
  against a number or a boolean) is not used and is a problem.

``derived_roles`` is what the snapshot proves (D61): ``identifier`` for a
primary or foreign key column, ``date`` for a temporal type. A role the
glossary sets wins over it.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from nl2sql_sdk.knowledge import DatasourceKnowledge
from nl2sql_sdk.schema import ColumnContract, SchemaSnapshot, TableContract


def _tables(snapshot: SchemaSnapshot) -> List[TableContract]:
    return list(snapshot.contract.tables.values())


def resolve_table(name: str, snapshot: SchemaSnapshot) -> Tuple[Optional[TableContract], Optional[str]]:
    """The one table ``name`` names, or None and why: ``"missing"`` or ``"ambiguous: <schemas>"``."""
    wanted = name.strip().lower()
    schema, _, table = wanted.rpartition(".")
    found = [t for t in _tables(snapshot) if t.table.table_name.lower() == table
             and (not schema or (t.table.schema_name or "").lower() == schema)]
    if len(found) == 1:
        return found[0], None
    if not found:
        return None, "missing"
    return None, "ambiguous: " + ", ".join(sorted(t.table.schema_name for t in found))


def resolve_column(name: str, snapshot: SchemaSnapshot
                   ) -> Tuple[Optional[TableContract], Optional[ColumnContract], Optional[str]]:
    """The table and column ``table.column`` (or ``schema.table.column``) names, or why it does not resolve."""
    table_name, _, column_name = name.strip().rpartition(".")
    if not table_name:
        return None, None, "not table.column"
    table, why = resolve_table(table_name, snapshot)
    if table is None:
        return None, None, why
    column = next((c for c in table.columns.values() if c.name.lower() == column_name.lower()), None)
    return (table, column, None) if column is not None else (table, None, "missing")


def table_label(table: TableContract, snapshot: SchemaSnapshot) -> str:
    """How a table is written: its bare name, or ``schema.table`` when the bare name is in two schemas."""
    name = table.table.table_name
    same = [t for t in _tables(snapshot) if t.table.table_name.lower() == name.lower()]
    return name if len(same) == 1 else f"{table.table.schema_name}.{name}"


def derived_roles(snapshot: SchemaSnapshot, *, dialect: Optional[str]) -> Dict[str, str]:
    """``table.column`` to the role the snapshot proves: ``identifier`` (a key) or ``date`` (a temporal type)."""
    from nl2sql.aggregation.join_keys import TEMPORAL
    from nl2sql.pipeline.nodes.validator.join_keys import column_category

    roles: Dict[str, str] = {}
    for table in _tables(snapshot):
        label = table_label(table, snapshot)
        keys = {c.lower() for fk in table.foreign_keys for c in fk.constrained_columns}
        for column in table.columns.values():
            if column.is_primary_key or column.name.lower() in keys:
                roles[f"{label}.{column.name}"] = "identifier"
            elif column_category(column.data_type, dialect) == TEMPORAL:
                roles[f"{label}.{column.name}"] = "date"
    return roles


def _missing(item: str, what: str, name: str, why: str, datasource_id: str) -> str:
    if why.startswith("ambiguous"):
        schemas = why.split(": ", 1)[1]
        return (f"{item}: '{name}' names a table in more than one schema ({schemas}); write it as "
                f"schema.table. It is not used.")
    return f"{item}: no {what} '{name}' in {datasource_id}'s indexed schema. It is not used."


def problems(knowledge: DatasourceKnowledge, snapshot: SchemaSnapshot, *, dialect: Optional[str]) -> List[str]:
    """Every name in ``knowledge`` that does not resolve, and every join whose types cannot be compared."""
    from nl2sql.aggregation.join_keys import CATEGORY_NOUN, OTHER, coercible
    from nl2sql.pipeline.nodes.validator.join_keys import column_category

    ds = snapshot.contract.datasource_id
    found: List[str] = []

    def table(item: str, name: str) -> None:
        if resolve_table(name, snapshot)[0] is None:
            found.append(_missing(item, "table", name, resolve_table(name, snapshot)[1], ds))

    def column(item: str, name: str) -> Optional[ColumnContract]:
        _, contract, why = resolve_column(name, snapshot)
        if contract is None:
            found.append(_missing(item, "column", name, why, ds))
        return contract

    for name in knowledge.tables:
        table(f"tables.{name}", name)
    for name in knowledge.columns:
        column(f"columns.{name}", name)
    for join in knowledge.joins:
        sides = [[join.from_] if isinstance(join.from_, str) else join.from_,
                 [join.to] if isinstance(join.to, str) else join.to]
        label = "->".join(",".join(side) for side in sides)
        resolved = [[column(f"joins.{label}", name) for name in side] for side in sides]
        if len(sides[0]) != len(sides[1]):
            found.append(f"joins.{label}: the two sides name a different number of columns. It is not used.")
            continue
        for (left_name, left), (right_name, right) in zip(zip(sides[0], resolved[0]), zip(sides[1], resolved[1])):
            if left is None or right is None:
                continue
            a, b = column_category(left.data_type, dialect), column_category(right.data_type, dialect)
            if OTHER not in (a, b) and not coercible(a, b):
                found.append(f"joins.{label}: {left_name} is {CATEGORY_NOUN[a]} and {right_name} is "
                             f"{CATEGORY_NOUN[b]}; they cannot be compared, so the join is not used.")
    for term in knowledge.terms:
        for name in term.tables:
            table(f"terms.{term.term}", name)
        for name in term.columns:
            column(f"terms.{term.term}", name)
    for rule in knowledge.rules:
        for name in rule.tables:
            table(f"rules.{rule.id}", name)
    return found
