"""``ChartSpec``: our own small typed chart, with Vega-Lite as its export (D110).

Four kinds: ``line``, ``bar``, ``big_number`` and ``table``. No colour is ever
set; the renderer's theme decides. Choosing a chart from a result and the
Vega-Lite export arrive with K1 (docs/architecture/wave_c.md, section 5); this
module holds the shapes. It imports only pydantic, so a model that carries a
``ColumnKind`` costs nothing to import.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import Field

from nl2sql.common.strict import StrictModel

# Wave A's D30 categories.
ColumnKind = Literal["number", "text", "temporal", "boolean", "other"]


class Encoding(StrictModel):
    field: str  # a result column
    kind: ColumnKind
    title: str  # the column, humanised: "total_amount" -> "Total amount"


class ChartData(StrictModel):
    trace_id: str
    result_id: str
    columns: List[str]
    rows: List[List[Any]]  # at most settings.charts.max_points
    truncated: bool  # the result had more rows than were charted


class ChartSpec(StrictModel):
    kind: Literal["line", "bar", "big_number", "table"]
    title: str  # the question that ran
    x: Optional[Encoding] = None  # line: temporal; bar: the category
    y: List[Encoding] = Field(default_factory=list)  # one or more measures
    series: Optional[Encoding] = None  # line only: a category split into lines
    value: Optional[Encoding] = None  # big_number
    data: ChartData
    takeaway: Optional[str] = None
    reason: str  # why this kind: "one date column and one number"

    def to_vega_lite(self) -> Optional[Dict[str, Any]]:
        """The Vega-Lite v5 spec with the rows inline; None for a table. Arrives with K1."""
        raise NotImplementedError("ChartSpec.to_vega_lite arrives with K1 (docs/architecture/wave_c.md, row K1).")
