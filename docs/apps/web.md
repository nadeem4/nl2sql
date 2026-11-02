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

## Dashboards and workspaces

The **Dashboards** page (`#/dashboards`, and `#/dashboards/{id}` for one dashboard) shows whole dashboards, which
the chat builds as drafts and a person pins into a workspace. It calls the [dashboards and workspaces
routes](../api/rest/dashboards.md) and never a model.

- **Workspaces** are listed on the left: create, rename, and delete (an empty one only; the server's sentence is shown
  otherwise). The chosen workspace lists its saved dashboards; **Drafts** lists those not yet pinned.
- **Opening** a dashboard only reads: each chart appears with its stored result and how old it is ("Refreshed 10 min
  ago"). The charts the server reports as due are then refreshed one at a time. **Refresh** re-runs every chart.
- **Charts** are drawn with the same chart card as the chat. A chart that cannot show says why: *needs fixing* (what
  broke, with **Remove this chart**), *denied* (you may not read its data), *needs approval* (the reason, a link to
  Approvals, and **Run again** once granted), or no rows.
- **Filters** apply values to every chart: a list of a column's values for an `in` filter, two ends for a `between`
  filter. **Apply** shows the filtered result and stores nothing; **Clear** brings back the stored results.
- **Schedule** sets how often the dashboard refreshes itself, or stops it. An interval under the server's minimum is
  refused with its own sentence.
- **Edit** turns on, per chart: rename, chart type (automatic, line, bar, big number, table), move earlier or later,
  wider or narrower, taller or shorter, and remove. It also renames the dashboard, **moves** it to another workspace
  (or **pins** a draft into one), removes a filter, and deletes the dashboard. The grid has four columns; charts are
  packed in order, so an arrangement never overlaps. What data a chart shows is changed in chat, not here.

