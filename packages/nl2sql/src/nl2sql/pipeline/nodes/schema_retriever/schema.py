import json
import re
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
    # True for a table the datasource lists in ``preferred_tables``; None (and
    # so absent from the prompt) otherwise.
    preferred: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


# Fields the planner and refiner never see. schema_version is bookkeeping. Of a
# column's stats, sample_values leave (they match literal filters like 'Rock' or
# 'USA'), and so does range, [min, max], for a date, time or numeric column that
# is not a key: without it "this year" on data that ends in 2013 plans a filter
# that is certain to be empty. Null percentage and distinct count stay in the
# snapshot.
_NOT_SENT = {"schema_version"}

_RANGE_TYPES = re.compile(r"(?i)date|time|int|numeric|decimal|real|float|double|money|number")
# A date or timestamp kept as text, as SQLite keeps every date.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _is_date_text(value: Any) -> bool:
    return isinstance(value, str) and bool(_ISO_DATE.match(value))


def _keys(row: Dict[str, Any]) -> set:
    keys = set(row.get("primary_key") or [])
    for rel in row.get("relationships") or []:
        if isinstance(rel, dict):
            keys.update(rel.get("from_columns") or [])
    return keys


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
    keys = _keys(row)
    columns = []
    for column in row.get("columns") or []:
        stats = column.pop("stats", None) or {}
        lo, hi = stats.get("min_value"), stats.get("max_value")
        date_text = _is_date_text(lo) and _is_date_text(hi)
        if stats.get("sample_values") and not date_text:
            column["sample_values"] = stats["sample_values"]
        typed = bool(_RANGE_TYPES.search(column.get("type") or ""))
        if lo is not None and hi is not None and (typed or date_text) and column["name"] not in keys:
            column["range"] = [lo, hi]
        columns.append(column)
    row["columns"] = columns
    return _prune(row)


def render_schema_for_prompt(tables: List[Table]) -> str:
    """The schema block the planner and refiner prompts carry.

    One compact JSON object per table, preferred tables first and then by name,
    keys sorted, so the same snapshot and role always render byte-identically:
    the block sits in the cached prompt prefix. Empty fields are dropped, so a
    table that is not preferred carries no ``preferred`` key.
    """
    rows = sorted(
        (_table_for_prompt(t) for t in tables),
        key=lambda r: (not r.get("preferred"), r.get("name", "")),
    )
    return "\n".join(
        json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str) for r in rows
    )
