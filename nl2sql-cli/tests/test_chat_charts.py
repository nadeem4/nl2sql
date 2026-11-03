"""``nl2sql_cli.chat.charts``: a ``ChartSpec`` drawn as text, with no terminal needed."""
from nl2sql import ChartData, ChartSpec, Encoding

from nl2sql_cli.chat.charts import bar_lines, big_number_text, line_series


def _spec(kind, columns, rows, **encodings) -> ChartSpec:
    return ChartSpec(kind=kind, title="customers by country", reason="test", takeaway="The USA leads.",
                     data=ChartData(trace_id="t1", result_id="n1", columns=columns, rows=rows, truncated=False),
                     **encodings)


def test_bar_chart_text_has_one_row_per_category():
    spec = _spec("bar", ["country", "customers"], [["USA", 13], ["Canada", 6], ["Brazil", 0]],
                 x=Encoding(field="country", kind="text", title="Country"),
                 y=[Encoding(field="customers", kind="number", title="Customers")])

    lines = bar_lines(spec, width=20)

    assert len(lines) == 3
    assert lines[0].startswith("USA") and lines[0].endswith("13") and "█" * 20 in lines[0]
    assert "█" in lines[1] and lines[1].count("█") < lines[0].count("█")
    assert "█" not in lines[2] and lines[2].endswith("0")


def test_line_series_has_a_series_per_split_with_first_and_last():
    spec = _spec("line", ["month", "plan", "revenue"],
                 [["2024-01", "a", 1], ["2024-02", "a", 3], ["2024-01", "b", 5], ["2024-02", "b", 2]],
                 x=Encoding(field="month", kind="temporal", title="Month"),
                 y=[Encoding(field="revenue", kind="number", title="Revenue")],
                 series=Encoding(field="plan", kind="text", title="Plan"))

    series = line_series(spec)

    assert [(s.label, s.values, s.first, s.last) for s in series] == [("a", [1, 3], 1, 3), ("b", [5, 2], 5, 2)]


def test_a_line_without_a_split_is_one_series_named_for_its_measure():
    spec = _spec("line", ["month", "revenue"], [["2024-01", 1], ["2024-02", 4]],
                 x=Encoding(field="month", kind="temporal", title="Month"),
                 y=[Encoding(field="revenue", kind="number", title="Revenue")])

    [only] = line_series(spec)

    assert only.label == "Revenue" and only.values == [1, 4]


def test_big_number_is_the_value():
    spec = _spec("big_number", ["customers"], [[59]], value=Encoding(field="customers", kind="number", title="Customers"))

    assert big_number_text(spec) == "59"
