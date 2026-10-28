# Charts

!!! note "Arrives with K1 (Wave C, 0.9)"
    This page is a stub. The shapes (`ChartSpec`, `Encoding`, `ChartData`, `ColumnKind`) exist and
    `ChartSpec.to_vega_lite` and `engine.results.chart` raise `NotImplementedError` naming K1. The
    design is [Wave C, section 5](../architecture/wave_c.md#5-auto-charts).

An answer's rows become a chart without a model call: five rules choose a line, a bar, a big number or
a table from the result's column kinds (`number`, `text`, `temporal`, `boolean`, `other`) and, for a
single-sub-query run, the typed plan. The chart is data (`ChartSpec`); `to_vega_lite()` renders it for a
browser. See [Results API](../api/core/results.md).

`settings.charts` already accepts `max_points`, `max_categories` and `max_series`.