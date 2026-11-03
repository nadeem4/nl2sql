"""A ``ChartSpec`` as text: what a terminal can draw of the facade's chart (wave_d.md, section 7).

Pure functions over the spec, so they need no terminal: ``widgets.py`` puts their output on screen.
A ``line`` is one series per value of its split (or one for its measure), a ``bar`` is a row of block
characters per category, a ``big_number`` is its figure. No colour is chosen here; the theme decides.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from nl2sql import ChartSpec

BLOCK = "█"


@dataclass(frozen=True)
class Series:
    label: str
    values: List[float]
    first: Any
    last: Any


def _column(spec: ChartSpec, field: str) -> int:
    return spec.data.columns.index(field)


def line_series(spec: ChartSpec) -> List[Series]:
    """One series per split value, in order of appearance; one series named for the measure when there is no split."""
    measure = spec.y[0]
    values = _column(spec, measure.field)
    if spec.series is None:
        points = [row[values] for row in spec.data.rows]
        return [Series(measure.title, points, points[0], points[-1])] if points else []
    split = _column(spec, spec.series.field)
    grouped: Dict[str, List[Any]] = {}
    for row in spec.data.rows:
        grouped.setdefault(str(row[split]), []).append(row[values])
    return [Series(label, points, points[0], points[-1]) for label, points in grouped.items()]


def bar_lines(spec: ChartSpec, *, width: int = 30) -> List[str]:
    """``label  ████  value`` per category, the longest value filling ``width`` characters."""
    assert spec.x is not None
    labels = [str(row[_column(spec, spec.x.field)]) for row in spec.data.rows]
    values = [row[_column(spec, spec.y[0].field)] for row in spec.data.rows]
    top = max((v for v in values if isinstance(v, (int, float))), default=0) or 1
    pad = max((len(label) for label in labels), default=0)
    return [f"{label:<{pad}}  {BLOCK * round(width * value / top) if isinstance(value, (int, float)) and value > 0 else '':<{width}}  {value}"
            for label, value in zip(labels, values)]


def big_number_text(spec: ChartSpec) -> str:
    assert spec.value is not None
    return str(spec.data.rows[0][_column(spec, spec.value.field)])
