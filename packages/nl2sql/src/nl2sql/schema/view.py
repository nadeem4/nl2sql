"""A renderable view of a datasource's indexed schema snapshot.

It reads the snapshot the indexer wrote, not the database: what a client shows
is exactly what the planner is given.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _columns(table_contract, table_metadata) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, column in table_contract.columns.items():
        column_metadata = table_metadata.columns.get(name) if table_metadata else None
        out.append({
            "name": name,
            "type": column.data_type,
            "nullable": bool(column.is_nullable),
            "primary_key": bool(column.is_primary_key),
            "description": (column_metadata.description if column_metadata else None) or "",
        })
    return out


def _foreign_keys(table_contract) -> List[Dict[str, Any]]:
    return [
        {
            "columns": list(fk.constrained_columns),
            "references_table": fk.referred_table.table_name,
            "references_columns": list(fk.referred_columns),
        }
        for fk in table_contract.foreign_keys
    ]


def schema_view(datasource_id: str, snapshot: Optional[Any]) -> Dict[str, Any]:
    """Tables sorted by name, each with its columns, foreign keys, row count and description.

    ``snapshot`` is a ``SchemaSnapshot``, or None before the first index, which
    gives no tables.
    """
    if snapshot is None:
        return {"datasource_id": datasource_id, "tables": []}
    tables: List[Dict[str, Any]] = []
    for table_key, table_contract in snapshot.contract.tables.items():
        table_metadata = snapshot.metadata.tables.get(table_key)
        tables.append({
            "name": table_contract.table.table_name,
            "schema": table_contract.table.schema_name,
            "row_count": table_metadata.row_count if table_metadata else None,
            "description": (table_metadata.description if table_metadata else None) or "",
            "columns": _columns(table_contract, table_metadata),
            "foreign_keys": _foreign_keys(table_contract),
        })
    tables.sort(key=lambda t: t["name"])
    return {"datasource_id": datasource_id, "tables": tables}
