# Web app

The web app (`nl2sql-ui/`) is the one browser interface to the REST API. It is built into the API package and served
by `nl2sql-api serve`. This page covers what each area of the app does; areas are added as they ship.

## Charts, diagrams, reports

A chat answer can carry outputs. Each is drawn from data the API already holds, by the renderer for its kind
(`src/outputs/Output.jsx` picks it from the `ToolOutput`). A body the page cannot read is shown as text, never dropped.

| Output | What you see |
|---|---|
| Chart | A line, bar, big number or table from the engine's [`ChartSpec`](../charts/index.md), drawn as SVG sized to its card. Bars run horizontally so long labels stay readable. A **Table** switch shows the rows behind the chart, the takeaway sits under it, and a note says when only the first rows are drawn. **Download Vega-Lite** saves the same chart as a Vega-Lite file (not offered for a table). |
| Diagram | A [Mermaid](../charts/diagrams.md) diagram. The library loads on the first diagram only, in its own file, and follows the page theme (light or dark). **Source** shows the text. |
| Report | The Markdown the report tool wrote: headings, paragraphs, lists, tables, code and collapsible sections. Links open in a new tab; raw HTML is never rendered. **Download .md** saves the text. |
| Rows | The result table, paged from `/results/{trace_id}/{result_id}`, with **CSV** download. |

Charts use up to four series colours and then a neutral grey; every chart with two or more series has a legend, and
each mark has a hover title with its full value. The series colours keep 3:1 contrast against the card in both themes
(`src/outputs/palette.test.js`). Downloads are fetched with the page's own request headers, so they work with the
same keys and identity as every other call.
