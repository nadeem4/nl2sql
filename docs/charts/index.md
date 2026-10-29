# Charts

An answer's rows become a chart without a model call. `engine.results.chart(result, user_context=caller)` reads the
saved result, as the caller who asked it, and returns a `ChartSpec`: our own small typed chart (a line, a bar, a big number or a table),
which `to_vega_lite()` exports for a browser. See [Results API](../api/core/results.md).

```python
spec = engine.results.chart(result, user_context=caller)   # a QueryResult, or a trace_id
spec.kind, spec.reason, spec.takeaway
vega = spec.to_vega_lite()                   # None for a table
```

## The spec

| Field | Meaning |
|---|---|
| `kind` | `line`, `bar`, `big_number` or `table` |
| `title` | The question that ran (a run with one sub-query: its intent; a `trace_id` alone: `Result`) |
| `x`, `y`, `series`, `value` | `Encoding`s (`field`, `kind`, a humanised `title`) the kind uses: `x` the time or category, `y` one or more measures, `series` a category split into lines, `value` the big number |
| `data` | `ChartData`: `trace_id`, `result_id`, `columns`, `rows` (at most `charts.max_points`), `truncated` |
| `takeaway` | One sentence from a template, or `None` for a table |
| `reason` | Why this kind, for example "one date column and 1 number" |

## Column kinds

The artifact writer records each column's kind in the run's manifest from the saved file's types:
integer, decimal and float are `number`; date, time and timestamp `temporal`; string `text`; bool
`boolean`; anything else `other`. When the run had one sub-query, its plan refines them: a
`DATE_TRUNC` select item is `temporal`, a grouped `DATE_PART('year', x)` is `temporal` (a year axis),
an aggregate is a measure, and a number that is grouped on is a dimension. In a plan that has an aggregate,
a plain column that is not grouped on (an id) is a dimension too. With several sub-queries or
no plan, every `number` is a measure and everything else a dimension.

## The five rules

First match wins.

| # | Shape of the result | Chart |
|---|---|---|
| 1 | One row, one measure, at most one text column | `big_number` |
| 2 | One temporal dimension, one to `max_series` measures, at least two rows | `line`, x ascending |
| 3 | One temporal and one text dimension (at most `max_series` distinct values), one measure | `line` with `series` |
| 4 | One text or boolean dimension, one to three measures, two to `max_categories` rows | `bar`, in the plan's order if it ordered, else by the first measure descending |
| 5 | Anything else | `table` |

At most `max_points` rows are read. When the result has more, or was cut at the datasource row limit
(`ResultSummary.truncated`), `data.truncated` is true and the chart is a table, so no point is dropped silently. `kind=` forces a kind the data can draw; any other is a
`ValueError` naming the ones it can.

## Takeaways

| Chart | Sentence |
|---|---|
| `line` | "Revenue went from 100 in 2024-01-01 to 150 in 2024-03-01 (+50%)." For a series, the one with the largest last value. No change is given when an end is null or the start is zero. |
| `bar` | "Books has the highest sales: 50." (the category is written as the data has it) After a `SUM` or `COUNT` with no negative value: "..., 50% of the total shown." |
| `big_number` | "Total: 1,234.5." |
| `table` | none |

Numbers have thousands separators and at most three digits after the point.

## Vega-Lite

`to_vega_lite()` returns a Vega-Lite v5 spec with the rows inline (`data.values`): a `line` or `bar`
mark, a temporal, ordinal (whole-number years, times of day) or nominal `x`, a quantitative `y`, several measures
folded with a `fold` transform and told apart by a `color` field, and a `color` field for `series`.
It sets no colour, scheme, font or size: the renderer's theme decides.

## Settings

```yaml
# settings.yaml
charts:
  max_points: 1000
  max_categories: 20
  max_series: 5
```

## The `chart` chat tool

In a [conversation](../conversations/tools.md) the `chart` tool charts the rows of an answer: `turn`
(the latest turn with an answer when absent), `step` (the nth answer of the turn, from 1), `result_id`
and `kind`. Its output is a `chart` whose body is the `ChartSpec` as JSON, titled with the question.
It raises `ToolNotApplicable` when there is no answer with data, or its rows have expired.
