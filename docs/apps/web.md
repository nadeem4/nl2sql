# Web app

The web app is the browser front end of NL2SQL. It lives in `nl2sql-ui/` at the
repository root, is built into the API package, and talks to nothing but the
[REST API](../api/index.md) through `nl2sql-ui/src/api/http.js`. Anything the
page can do, an SDK or REST caller can do the same way.

Pages are reached by hash route: `#/chat`, `#/approvals`, `#/settings`,
`#/retrieval`, `#/pipeline`. A route nobody serves opens Home.

## Chat

`#/chat` (and `#/chat/<conversation id>`) is a [conversation](../conversations/index.md)
with the model-led chat. The model decides which steps to take; the page shows
what it did.

**The list and the transcript.** The left column lists your conversations
(`GET /conversations`), with **New conversation** and a delete button on each.
The transcript shows one block per stored turn. A new conversation starts on the
first question, so the address bar names it from then on and a reload comes back
to the same place. A new conversation also offers the datasource's starter
questions and, with more than one datasource, a choice of which one to ask.

**A turn streams.** A question is sent to `POST /conversations/{id}/turns/stream`.
While it runs, the page shows the run spine (the pipeline stations lit as each
one finishes) and the answer text as it arrives. The rows appear with the stored
turn, not before. The last
event carries the stored turn, which replaces the live view, so what stays on
the page is exactly what the server saved. **Stop** cancels the stream; during a tool or retry call, which cannot be
cancelled from the page, the button reads **Working** and is disabled. A second
turn sent while one is running is refused by the server with 409, and the page
says so.

**What an answered turn shows.** The answer text and the rows come first. Below
them, in this order:

- warnings about the result (truncated rows, withheld columns, and so on);
- **Why this answer**, a fold with the sections of the explanation (how the
  question was read, which tables and curated answers were used, what the
  planner chose; the planner's note is marked as the model's own words);
- **SQL** and **Plan**, the typed plan and the query it produced;
- **Trace and cost**, the run's stations, timings and token use;
- a rating (**Right** or **Wrong**, with a note), and a corrected SQL that
  **Send for review** files (`POST /feedback`, `POST /feedback/correct`).

**Only the last turn is live.** A clarification pick answers the conversation's
pending question, and **Run it now** re-runs the last question, so those buttons
show on the last turn only. Earlier turns keep their text. If you open another
conversation while a turn is running, its result is added to the conversation it
was sent for, not to the one on screen.

**Clarification.** When the model needs a decision, the turn shows its question
and an option for each reading. **Use this reading** sends `{choice: option id}`
as the next turn. **Remember this meaning** also files the pick, with an
optional term, as a proposal for review (`POST /knowledge/choices`).

**Approvals.** When a plan needs approval, the turn lists what is waiting. Once
every request is granted, **Run it now** calls `POST /conversations/{id}/retry`
with the approval ids. **Check again** reads the requests' state; a denied request says so and the turn stays stopped.

**More with this answer.** Buttons under a turn run the conversation's tools
(`GET /chat-tools`, `POST /conversations/{id}/tools/{name}`): **Chart**,
**Diagram**, **Report**, and **Pin to dashboard** when the conversation belongs
to a dashboard. A tool the model offers in its reply gets a button too. Tool
arguments use 1-based `turn` and `step` numbers. **Report** calls
`POST /conversations/{id}/tools/report`, which stores the report as a turn output
(the markdown is in `output.body`); the `GET /conversations/{id}/report` download
is not used by the page.

## Approvals

`#/approvals` lists the requests waiting for a decision (`GET /approvals?status=pending`),
each with the question, who asked, the plan, the SQL it would run and why it
stopped. **Grant** and **Deny** call the matching routes. The list also holds requests you
asked yourself; those show no buttons, since only an approver may decide them,
and the nav count leaves them out. The page learns your user id from `GET /meta`
(`caller.user_id`). On the hosted demo there is none, so it cannot tell them apart
and the server refuses a decision that is not yours, with its reason shown.

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

Removing a datasource asks first. Once a datasource is added, secrets typed into the form are cleared. Connect needs an administrator. `GET /meta` says whether you are one (`caller.admin`), so on the hosted demo, and for anyone who is not an administrator, the page says it is off instead of showing a form. If `/meta` is unavailable the page asks the server, and a 403 from it turns the page off the same way.

**Knowledge** (`#/knowledge`) shows, for one datasource:

- **What the engine knows:** terms, rules, joins, tables and columns. Tables and columns are marked with where their description or role came from (a knowledge file, the database, or derived). Any problems found while loading, and the schema.
- **Waiting for review** (administrators; the page does not ask for these unless `/meta` says you are one, and a 403 still hides them): the proposals from profiling and starter questions, grouped by kind. Each shows everything accepting would write (a role, a unit, code values, text), and the counts and checks that back it, never rows of data. Accept or reject one or many; "Edit and accept" lets you change a proposal's text first. Accepting writes to the knowledge files; the page names the files and shows any proposal the server refused and why.
- **Drift** (administrators): **Check for drift** compares each live database with its index and lists what changed (tables, columns, types, foreign keys) and what the change may break: verified queries, glossary items, policy rules and preferred tables. To bring a drifted index up to date, run `nl2sql index`.

## Source map

| Path | What it holds |
| --- | --- |
| `src/api/chat.js` | Every conversation, approval, feedback and trace call |
| `src/chat/spine.js` | Folds stream events into the live run spine |
| `src/chat/turns.js` | Reads a stored turn: answer, steps, pending questions, tool buttons |
| `src/pages/Chat.jsx` | The page: list, transcript, composer, streaming |
| `src/pages/Approvals.jsx` | The approvals queue |

Where the code and `docs/architecture/wave_d.md` differ, the code is right.
