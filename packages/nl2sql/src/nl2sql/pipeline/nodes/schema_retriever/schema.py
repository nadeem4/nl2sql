import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field



class Column(BaseModel):
    """Lightweight column schema for routing/planning."""

    name: str
    type: Optional[str] = None
    stats: Optional[Dict[str, Any]] = None
    description: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class Table(BaseModel):
    """Lightweight table schema for routing/planning."""

    name: str
    columns: List[Column] = Field(default_factory=list)
    description: Optional[str] = None
    primary_key: Optional[List[str]] = None
    foreign_keys: Optional[Dict[str, List[str]]] = None

    model_config = ConfigDict(extra="allow")


# Fields the planner and refiner never see. schema_version is bookkeeping, and
# only a column's sample_values (they match literal filters like 'Rock' or 'USA')
# leave its stats; min/max, null percentage and distinct count stay in the snapshot.
_NOT_SENT = {"schema_version"}


def _prune(value: Any) -> Any:
    """Drops None and empty strings, lists and dicts, recursively."""
    if isinstance(value, dict):
        pruned = {k: _prune(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def _table_for_prompt(table: Table) -> Dict[str, Any]:
    row = {k: v for k, v in table.model_dump().items() if k not in _NOT_SENT}
    columns = []
    for column in row.get("columns") or []:
        stats = column.pop("stats", None) or {}
        if stats.get("sample_values"):
            column["sample_values"] = stats["sample_values"]
        columns.append(column)
    row["columns"] = columns
    return _prune(row)


def render_schema_for_prompt(tables: List[Table]) -> str:
    """The schema block the planner and refiner prompts carry.

    One compact JSON object per table, tables sorted by name and keys sorted, so
    the same snapshot and role always render byte-identically: the block sits in
    the cached prompt prefix. Empty fields are dropped.
    """
    rows = sorted((_table_for_prompt(t) for t in tables), key=lambda r: r.get("name", ""))
    return "\n".join(
        json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str) for r in rows
    )
