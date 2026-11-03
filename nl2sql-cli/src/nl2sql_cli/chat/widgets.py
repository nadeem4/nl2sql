"""The terminal app's small widgets: plain text lines, the rows table, a chart drawn from its spec."""
from __future__ import annotations

from typing import Any, List, Optional, Sequence

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual.widgets import Sparkline

from nl2sql import ChartSpec

from nl2sql_cli.chat.charts import bar_lines, big_number_text, line_series


class Note(Static):
    """One block of plain text. It keeps the text it shows in ``text``, and never reads it as markup."""

    def __init__(self, text: str, *, classes: Optional[str] = None, id: Optional[str] = None) -> None:
        super().__init__(text, markup=False, classes=classes, id=id)
        self.text = text

    def set(self, text: str) -> None:
        self.text = text
        self.update(text)


def rows_table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> DataTable:
    table = DataTable(zebra_stripes=True, cursor_type="row")
    table.add_columns(*columns)
    table.add_rows([tuple("" if cell is None else str(cell) for cell in row) for row in rows])
    table.styles.height = min(len(rows) + 2, 14)
    return table


class ChartView(Vertical):
    """A ``ChartSpec`` as the terminal draws it: a figure, sparklines, bars or the table, then the takeaway."""

    DEFAULT_CSS = "ChartView { height: auto; margin: 1 0; }"

    def __init__(self, spec: ChartSpec) -> None:
        super().__init__()
        self.spec = spec

    def compose(self) -> ComposeResult:
        spec = self.spec
        yield Note(spec.title, classes="chart-title")
        if spec.kind == "big_number":
            yield Note(big_number_text(spec), classes="big-number")
        elif spec.kind == "line":
            for series in line_series(spec):
                yield Note(f"{series.label}: {series.first} to {series.last}")
                yield Sparkline([float(v) for v in series.values])
        elif spec.kind == "bar":
            yield Note("\n".join(bar_lines(spec)))
        else:
            yield rows_table(spec.data.columns, spec.data.rows)
        if spec.takeaway:
            yield Note(spec.takeaway, classes="takeaway")
