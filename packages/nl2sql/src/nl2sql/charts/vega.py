"""``ChartSpec`` as a Vega-Lite v5 spec, with the rows inline (D110).

No colour, font or size is ever set: the renderer's theme decides. A ``color`` encoding names a field
(a series, or the folded measures) and never a value or a scheme.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Dict, Optional

from nl2sql.charts.spec import ChartSpec, Encoding

SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"


def _json(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _type(encoding: Encoding, spec: ChartSpec) -> str:
    if encoding.kind == "temporal":
        # A year axis (DATE_PART) is whole numbers; Vega-Lite would read them as epoch milliseconds.
        index = spec.data.columns.index(encoding.field)
        first = next((row[index] for row in spec.data.rows if row[index] is not None), None)
        return "ordinal" if isinstance(first, (int, float)) and not isinstance(first, bool) else "temporal"
    return "quantitative" if encoding.kind == "number" else "nominal"


def _field(encoding: Encoding, spec: ChartSpec) -> Dict[str, Any]:
    return {"field": encoding.field, "type": _type(encoding, spec), "title": encoding.title}


def to_vega_lite(spec: ChartSpec) -> Optional[Dict[str, Any]]:
    """The Vega-Lite spec for ``spec``; None for a table (the renderer draws a table from ``data``)."""
    if spec.kind == "table":
        return None
    columns = spec.data.columns
    out: Dict[str, Any] = {
        "$schema": SCHEMA,
        "title": spec.title,
        "data": {"values": [{c: _json(v) for c, v in zip(columns, row)} for row in spec.data.rows]},
    }
    if spec.kind == "big_number":
        out["mark"] = {"type": "text"}
        out["encoding"] = {"text": {"field": spec.value.field, "type": "quantitative"}}
        return out

    encoding: Dict[str, Any] = {"x": _field(spec.x, spec)}
    if spec.kind == "bar":
        encoding["x"]["sort"] = None  # the rows are already in the chosen order
    if len(spec.y) > 1:
        out["transform"] = [{"fold": [e.field for e in spec.y], "as": ["measure", "value"]}]
        encoding["y"] = {"field": "value", "type": "quantitative"}
        encoding["color"] = {"field": "measure", "type": "nominal"}
        if spec.kind == "bar":
            encoding["xOffset"] = {"field": "measure"}
    else:
        encoding["y"] = {"field": spec.y[0].field, "type": "quantitative", "title": spec.y[0].title}
    if spec.series is not None:
        encoding["color"] = {"field": spec.series.field, "type": "nominal", "title": spec.series.title}
    out["mark"] = spec.kind
    out["encoding"] = encoding
    return out
