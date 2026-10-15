"""The structural diff between two schema contracts of one datasource, and what a caller may see of it.

docs/architecture/wave_a.md, section 10. It compares exactly what
``schema/fingerprint.py`` covers, keyed the same way (tables by their key in
the contract, columns by name), so for two contracts of one datasource the
diff is empty exactly when the fingerprints are equal. A rename is a drop and
an add: nothing can tell a rename from a drop without asking someone.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

from nl2sql_sdk.schema import (
    ColumnChange,
    ColumnContract,
    ColumnRef,
    ForeignKeyRef,
    SchemaContract,
    SchemaDiff,
    TableContract,
    TableRef,
)

# Whether a caller may read ``schema.table``: ``Scope.allows``.
Allows = Callable[[Optional[str], str], bool]

_FkKey = Tuple[Tuple[str, ...], str, str, Tuple[str, ...]]


def diff_contracts(old: SchemaContract, new: SchemaContract, *, from_version: str,
                   to_version: Optional[str]) -> SchemaDiff:
    """What changed from ``old`` to ``new``. ``to_version`` is None when ``new`` is the live database."""
    diff = SchemaDiff(datasource_id=new.datasource_id, from_version=from_version, to_version=to_version)
    if old.engine_type != new.engine_type:
        diff.engine_type = (old.engine_type, new.engine_type)
    for key in sorted(set(new.tables) - set(old.tables)):
        diff.tables_added.append(new.tables[key].table)
    for key in sorted(set(old.tables) - set(new.tables)):
        diff.tables_dropped.append(old.tables[key].table)
    for key in sorted(set(old.tables) & set(new.tables)):
        _diff_table(old.tables[key], new.tables[key], diff)
    return diff


def _diff_table(old: TableContract, new: TableContract, diff: SchemaDiff) -> None:
    ref = new.table
    old_columns = {c.name: c for c in old.columns.values()}
    new_columns = {c.name: c for c in new.columns.values()}
    for name in sorted(set(new_columns) - set(old_columns)):
        diff.columns_added.append(ColumnRef(table=ref, column_name=name))
    for name in sorted(set(old_columns) - set(new_columns)):
        diff.columns_dropped.append(ColumnRef(table=old.table, column_name=name))
    for name in sorted(set(old_columns) & set(new_columns)):
        changes = _column_changes(old_columns[name], new_columns[name])
        if changes:
            diff.columns_changed.append(ColumnChange(table=ref, column=name, changes=changes))

    old_fks, new_fks = _foreign_keys(old), _foreign_keys(new)
    for key, fk in sorted(new_fks.items()):
        for _ in range(fk[1] - old_fks.get(key, (fk[0], 0))[1]):
            diff.foreign_keys_added.append(fk[0])
    for key, fk in sorted(old_fks.items()):
        for _ in range(fk[1] - new_fks.get(key, (fk[0], 0))[1]):
            diff.foreign_keys_dropped.append(fk[0])


def _column_changes(old: ColumnContract, new: ColumnContract) -> Dict[str, tuple]:
    changes = {}
    if old.data_type != new.data_type:
        changes["type"] = (old.data_type, new.data_type)
    if old.is_nullable != new.is_nullable:
        changes["nullable"] = (old.is_nullable, new.is_nullable)
    if old.is_primary_key != new.is_primary_key:
        changes["pk"] = (old.is_primary_key, new.is_primary_key)
    return changes


def _foreign_keys(table: TableContract) -> Dict[_FkKey, Tuple[ForeignKeyRef, int]]:
    """The table's foreign keys as the fingerprint sees them (columns sorted), each with how many times it occurs."""
    refs = {}
    counts: Counter = Counter()
    for fk in table.foreign_keys:
        key = (tuple(sorted(fk.constrained_columns)), fk.referred_table.schema_name,
               fk.referred_table.table_name, tuple(sorted(fk.referred_columns)))
        counts[key] += 1
        refs[key] = ForeignKeyRef(table=table.table, columns=sorted(fk.constrained_columns),
                                  referred_table=fk.referred_table, referred_columns=sorted(fk.referred_columns))
    return {key: (refs[key], count) for key, count in counts.items()}


def visible_diff(diff: SchemaDiff, allows: Allows) -> SchemaDiff:
    """``diff`` without anything about a table ``allows`` refuses, so a denied table's name never reaches a caller.

    A foreign key is left out when either of its tables is refused, as the
    caller's schema view leaves it out. The ``engine_type`` change names no
    table and stays.
    """
    def readable(ref: TableRef) -> bool:
        return allows(ref.schema_name, ref.table_name)

    return diff.model_copy(update={
        "tables_added": [t for t in diff.tables_added if readable(t)],
        "tables_dropped": [t for t in diff.tables_dropped if readable(t)],
        "columns_added": [c for c in diff.columns_added if readable(c.table)],
        "columns_dropped": [c for c in diff.columns_dropped if readable(c.table)],
        "columns_changed": [c for c in diff.columns_changed if readable(c.table)],
        "foreign_keys_added": _readable_fks(diff.foreign_keys_added, readable),
        "foreign_keys_dropped": _readable_fks(diff.foreign_keys_dropped, readable),
    })


def visible_contract(contract: SchemaContract, allows: Allows) -> SchemaContract:
    """``contract`` without the tables ``allows`` refuses, nor the foreign keys that refer to one.

    Its fingerprint is what ``engine.index.versions`` shows a caller, so a
    change only to a denied table looks to it like a statistics-only one.
    """
    tables = {}
    for key, table in contract.tables.items():
        if not allows(table.table.schema_name, table.table.table_name):
            continue
        fks = [fk for fk in table.foreign_keys
               if allows(fk.referred_table.schema_name, fk.referred_table.table_name)]
        tables[key] = table if len(fks) == len(table.foreign_keys) else table.model_copy(update={"foreign_keys": fks})
    return contract.model_copy(update={"tables": tables})


def _readable_fks(fks: List[ForeignKeyRef], readable: Callable[[TableRef], bool]) -> List[ForeignKeyRef]:
    return [fk for fk in fks if readable(fk.table) and readable(fk.referred_table)]
