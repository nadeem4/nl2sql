"""The one-line takeaway of a chart: a template filled from the data, never a model call (D111)."""
from __future__ import annotations

from typing import Any, List, Optional

from nl2sql.charts.spec import ChartSpec


def number(value: Any) -> str:
    """A number with thousands separators and at most three digits after the point; ``n/a`` for null."""
    if value is None:
        return "n/a"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    return f"{round(value, 3):,.3f}".rstrip("0").rstrip(".")


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + "."


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def takeaway(spec: ChartSpec, share_of_total: bool = False) -> Optional[str]:
    """The sentence for ``spec``'s rows (already in chart order), or None for a table.

    ``share_of_total`` says the first measure is a ``SUM`` or ``COUNT``, so a bar may say its share.
    """
    columns = spec.data.columns
    rows = spec.data.rows
    if not rows:
        return None
    if spec.kind == "big_number":
        return _sentence(f"{spec.value.title}: {number(rows[0][columns.index(spec.value.field)])}")
    if spec.kind == "line":
        return _line(spec, columns, rows)
    if spec.kind == "bar":
        return _bar(spec, columns, rows, share_of_total)
    return None


def _line(spec: ChartSpec, columns: List[str], rows: List[List[Any]]) -> str:
    x, y = columns.index(spec.x.field), columns.index(spec.y[0].field)
    points = rows
    if spec.series is not None:
        s = columns.index(spec.series.field)
        best, best_last = None, None
        for name in dict.fromkeys(row[s] for row in rows):
            mine = [row for row in rows if row[s] == name]
            last = mine[-1][y]
            if _numeric(last) and (best_last is None or last > best_last):
                best, best_last = mine, last
        points = best or rows
    first, last = points[0], points[-1]
    text = f"{spec.y[0].title} went from {number(first[y])} in {first[x]} to {number(last[y])} in {last[x]}"
    if _numeric(first[y]) and _numeric(last[y]) and first[y] != 0:
        text += f" ({(last[y] - first[y]) / abs(first[y]):+.0%})"
    return _sentence(text)


def _bar(spec: ChartSpec, columns: List[str], rows: List[List[Any]], share_of_total: bool) -> Optional[str]:
    x, y = columns.index(spec.x.field), columns.index(spec.y[0].field)
    values = [row[y] for row in rows if _numeric(row[y])]
    if not values:
        return None
    top = max((row for row in rows if _numeric(row[y])), key=lambda row: row[y])
    text = f"{top[x]} has the highest {spec.y[0].title.lower()}: {number(top[y])}"
    total = sum(values)
    if share_of_total and min(values) >= 0 and total > 0:
        text += f", {top[y] / total:.0%} of the total shown"
    return _sentence(text)
