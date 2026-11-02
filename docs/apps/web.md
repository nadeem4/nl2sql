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

## Connect and review

**Connect** (`#/connect`) adds a datasource to the running server.

1. Pick a type. The form is built from that adapter's own connection schema, so a third-party adapter gets a form with no change to the app. An adapter that is not installed shows its install hint.
2. Fill in the connection. Secret fields are password inputs: type an `${env:NAME}` reference, not the secret itself. Less common settings sit under Advanced.
3. **Test connection** reports Connected, or the stage that failed (configuration, driver, network, sign-in, database, permissions) with the reason and the fix.
4. **Add** registers the datasource. Tick "Save to the project config" to write it to `nl2sql.yaml`. Where the config is read-only (the Docker image), the datasource is added but not saved: it works until the server restarts, and you add it to the mounted config to keep it.
5. After Add the page offers **Index**, **Profile** (optionally drafting descriptions with the model, under a spending cap you set) and **Suggest starter questions**, which are filed as proposals for review. Each job shows its progress and can be cancelled.

Removing a datasource asks first. Once a datasource is added, secrets typed into the form are cleared. Connect needs an administrator. On the hosted demo, and for anyone who is not an administrator, the page says it is off instead of showing a form.

**Knowledge** (`#/knowledge`) shows, for one datasource:

- **What the engine knows:** terms, rules, joins, tables and columns. Tables and columns are marked with where their description or role came from (a knowledge file, the database, or derived). Any problems found while loading, and the schema.
- **Waiting for review** (administrators): the proposals from profiling and starter questions, grouped by kind. Each shows everything accepting would write (a role, a unit, code values, text), and the counts and checks that back it, never rows of data. Accept or reject one or many; "Edit and accept" lets you change a proposal's text first. Accepting writes to the knowledge files; the page names the files and shows any proposal the server refused and why.
- **Drift** (administrators): **Check for drift** compares each live database with its index and lists what changed (tables, columns, types, foreign keys) and what the change may break: verified queries, glossary items, policy rules and preferred tables. To bring a drifted index up to date, run `nl2sql index`.
