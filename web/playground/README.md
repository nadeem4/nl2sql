# nl2sql playground UI

The React source for the page `nl2sql demo` serves.

## Pages

Three pages, named across the top of each of them by one header nav
(`.nav`, one `#nav-ask`, `#nav-settings`, `#nav-retrieval` link each). Every
page states its title and one line saying what it does, then gives its content
the full width.

| Route | Page | What it holds |
| --- | --- | --- |
| `#/` | Ask | the composer, the rail (Search index and Database) and the run |
| `#/settings` | Settings | `#settings-panel`: keys and a model per step |
| `#/retrieval` | Retrieval inspector | `#retrieval-panel`: the inspector |

`src/router.js` is the whole of it: `pageFromHash` reads the hash, `navItems`
builds the nav, and `createRouter` follows `hashchange`. A route nobody serves
reads as `#/`, so Back and Forward always land somewhere. The nav links are
plain `<a href="#/...">`, so the keyboard reaches them and the browser keeps
the history; the current one carries `aria-current="page"` and the accent under
the top bar's rule. Nothing in the page links to a bare fragment -- the skip
control and the index warning's **Rebuild it** move focus instead -- because
the hash belongs to the router.

**The run is not lost.** The question, the answer, the Debug choice and every
`/api` reply are held in `App`, above the page switch, so Settings and back
leaves the run exactly as it was. Nothing extra is written to storage for it:
the address bar already restores the page on a reload.

**Where a page is off** (Settings, Retrieval and Rebuild share one gate: a demo
project, on a loopback host or with `--allow-settings`), its nav item stays and
is marked `off`; opening it is how you read the reason
(`#settings-unavailable`, `#retrieval-unavailable`).

**Hosted mode** (`nl2sql demo --hosted`, `meta.hosted`) is a third state, not a
page that is off. Settings keeps its nav item unmarked and shows
`#hosted-key-heading` instead: a key form that writes only to this tab's
`sessionStorage` (`src/hostedKey.js`), from where `askHeaders` sends it as the
`X-NL2SQL-Api-Key` header on `/api/ask` and nowhere else. Nothing about the key
is posted to the server, so there is no save to succeed or fail; the copy on the
page says exactly that, plus the limits from `meta.limits` and how to run the
demo locally instead. Rebuild and answer ratings are off, and the retrieval
inspector stays on. See [Hosted demo](../../docs/deployment/hosted-demo.md).

`src/router.test.js` covers the default route, deep links, an unknown route,
Back and Forward, and that routing touches nothing but the hash.

## What the page shows

- **Database** (left rail, or below the run on a narrow window): the indexed
  schema from `/api/schema`, visible before any question. It is one database's,
  so where the index holds several the summary opens with `One of 3 databases`
  (`databases`, counted from the index health in `App`); with one it reads as
  it always has. Each table shows its row count and the tables it refers to;
  open one for columns, types, keys and foreign keys. Tables the current plan
  reads are marked `in plan`; tables the role was refused are marked
  `refused for <role>`.
- **Search index** (`#index-panel`, top of the rail): from `GET /api/index`,
  the vector index the resolver searches, which the Database below does not
  show. A status line (`#index-status`), entries by type (`#index-counts`), the
  schema version (`#index-version`) and when it was built (`#index-built`). The
  index covers every database, so the heading names one only when there is one;
  with several, `#index-sources` lists them ("Covers chinook, support and
  webanalytics", from `sourceNames` and `joinNames` in `indexHealth.js`) and
  Rebuild's help names the single database it rebuilds.
  When the index is empty, missing or out of date, the panel turns to the fault
  colour, a stale index lists why, and a warning under the top bar
  (`#index-warning`) puts the keyboard on the button, or, from another page,
  leads back to Ask. **Rebuild** (`#index-rebuild`) is
  always offered; it posts to `POST /api/index/rebuild` and the page polls
  `GET /api/index` for its steps (`#index-progress`) until it ends, then
  re-reads the schema. **Write descriptions with the LLM** (`#index-enrich`) is
  off by default and disabled without a key. A failure shows `#index-error`.
  Where Rebuild is off (a non-loopback `--host` without `--allow-settings`) the
  panel says why (`#index-unavailable`). A demo folder written by an older
  engine shows `#index-folder-warning`.
- **Mode line** (top bar): live or replay. In replay it states how many guided
  questions the loaded recordings answer (`recorded_questions` from
  `/api/meta`), or, with none, that replay mode has no recorded answers and a key
  is needed. A question replay has no answer for shows "No recorded answer for
  this question. Add an API key to ask it live." (`replay_miss` from `/api/ask`).
- **Composer** (Ask page): the question box, the role selector
  (`#role-select`), **Plan only** (`#plan-only`), **Debug** (`#debug-toggle`)
  and the guided questions from `/api/meta`. The demo registers three
  databases, so the questions are shown in one `.guided-group` per datasource,
  each headed by its id (`.guided-source`) in the quiet mono the rail uses, in
  the order `/api/meta` sends them in `question_groups`. With a single
  database there is nothing to tell apart, so no heading is printed and the
  question box names that database; with several it does not, because the
  resolver picks. `src/questions.js` does the grouping and falls back to the
  flat `questions` list when a server sends no `question_groups`.
- **The run**: one spine, read top to bottom. Question, Plan (`#pane-plan`),
  Checks (`#pane-validation`), SQL (`#pane-sql`), Rows (`#pane-rows`), Cost &
  time (`#pane-usage`). The checks sit across the spine as a gate: when a plan is
  refused the page says so there, names the tables the role may not read, and
  the SQL and Rows stations show that nothing was written or run. A retried plan
  shows the rejected attempt and the refiner's feedback.
- **Cost & time**: the question's totals (LLM calls, input, cached and output
  tokens, time waiting on the model, total time). With **Debug** on, a per-node
  table follows: one row per node that ran, in execution order, code nodes
  included, with calls, input, cached, output and reasoning tokens, LLM time and
  node wall-clock time. The SQL agent is a subgraph, so its nodes are nested
  under it and its time includes theirs. When a sub-query's plan came from the
  plan cache (`plan_source: "cache"` in `/api/ask`), Debug also shows
  `#plan-cache-hit`: no planner call was made, and the plan was validated again
  for the role. Debug is on by default; the choice is
  kept in `localStorage` (`nl2sql.playground.debug`) and the page works the same
  when storage is unavailable.
- **Node drill-down**: when the run wrote a trace (`trace_path` in the
  response; the demo writes one for every run), each node name in the Debug
  table is a button. It fetches the trace once from `GET /api/trace/{trace_id}`
  and shows that node's executions (attempts and sub-queries) with errors and
  warnings, the state it read, the update it returned and, for LLM nodes, the
  exact prompt, the raw response and the parsed result, each folded. A
  **Download trace** link sits above the table.
- **Was this answer right?** (`#feedback`, below Cost & time once a run is
  back): **👍 Right** (`#feedback-up`) and **👎 Wrong** (`#feedback-down`) save
  a rating at once through `POST /api/feedback`; a note is optional, from the
  quick choices or typed (`#feedback-note`, up to 280 characters), saved with
  **Save note** (`#feedback-save`). The page sends only the trace id, the
  rating and the note; the server stores the question, role, SQL and models
  from its own copy of the run. Whether it is on comes from `GET /api/feedback`;
  where it is off (the same rule as Settings, or `FEEDBACK_ENABLED=false`) a
  line says why. See
  [Feedback and Signals](../../docs/observability/feedback.md).

- **Retrieval** (`#nav-retrieval` in the header nav, route `#/retrieval`; the
  page's content is `#retrieval-panel`): the Retrieval inspector. Text to embed
  (`#retrieval-query`), **Search** (`#retrieval-search`), picks `k`
  (`#retrieval-k`, the pool is shown as `4 * k`), lambda (`#retrieval-lambda`),
  a datasource filter (`#retrieval-datasource`) and one checkbox per entry type
  (`#retrieval-type-table`, `-column`, `-datasource`, `-join`, `-metric`). It
  posts to `POST /api/retrieval`; after the first search every knob searches
  again. The result (`#retrieval-result`) is the pool nearest first, with
  similarity, MMR pick order, the score each pick won with and its overlap with
  earlier picks, each entry's embedded text, and **Copy as text**
  (`#retrieval-copy`) for diffing two runs. A failure shows `#retrieval-error`.
  Where it is off (the same rule as Settings) it says why
  (`#retrieval-unavailable`), and the nav marks it.
- **Retrieval in the drill-down**: for `datasource_resolver` and
  `schema_retriever`, the node drill-down also shows the run's retrieval record
  from the trace: the text embedded, each search's pool with the picks marked,
  the entries MMR passed over, and the tables sent to the planner; or why no
  search ran.
- **Settings** (`#nav-settings` in the header nav, route `#/settings`; the
  page's content is `#settings-panel`). **API key** (`#settings-key`, `#settings-save-key`) shows the key in use
  only in masked form (`#settings-key-current`); saving one writes it to the
  demo project's `.env.demo` and turns replay into live without a restart, and
  the mode line follows. **Model for each step** has one selector per LLM node
  (`#model-datasourceresolver`, `#model-decomposer`, `#model-astplanner`,
  `#model-refiner`, `#model-answersynthesizer`) with a Default option, and
  **Save models** (`#settings-save-models`) writes the changed ones to
  `configs/llm.demo.yaml`.
  Choosing a model that runs without a temperature shows
  `#settings-temperature-note`. The model list comes from `GET /api/settings`;
  the page names no model itself. When the server has settings off (a
  non-loopback `--host` without `--allow-settings`) the page shows the reason
  (`#settings-unavailable`) instead of a form, and the nav marks it.

Every run station renders the `/api/ask` response; nothing is computed
server-side for the page.

## Rebuild

```bash
cd web/playground
npm install
npm test
npm run build
```

`npm run build` writes a single self-contained `index.html` (JS, CSS and fonts
inlined by `vite-plugin-singlefile`) into:

```
packages/nl2sql/src/nl2sql/cli/demo/playground/static/index.html
```

**That build output is committed on purpose.** The wheel has to contain the
finished page so `pip install "nl2sql-engine[demo]"` followed by `nl2sql demo`
works with no Node installed, and so CI never needs a JavaScript toolchain. The
trade is that a UI change is not live until you rebuild and commit the result --
if you edit anything under `src/`, run `npm run build` and commit
`static/index.html` in the same change.

`npm test` runs the pure helpers in `src/run.js` (SQL line breaks, the per-node
ledger, refused-table parsing, the trace drill-down helpers), `src/settings.js`
(model options, which nodes changed, which chosen models run without a
temperature), `src/indexHealth.js` (entry counts in plain words, the status
line, relative build times), `src/router.js` (which page a hash names, the nav
rows, and the router over `hashchange`), `src/questions.js` (the guided
questions grouped by datasource) and `src/retrieval.js` (the MMR summary line, picks
in order, entries passed over, the copyable text form) and `src/feedback.js`
(when a run can be rated, the request body, the saved line) with Node's built-in test runner; there is no test dependency.

## Fonts and offline use

The page makes no network request other than its own API routes: no CDN, no
Google Fonts. The two typefaces are bundled at build time from `@fontsource`
(dev dependencies), Latin subset only, and inlined into the page:

- **Schibsted Grotesk** (variable) for the interface;
- **Fragment Mono** for plans, SQL, table and column names, and numbers.

Both are SIL Open Font License. The built page is about 300 KB.

## Develop

```bash
npm run dev
```

Vite serves the page on its own port. Run `nl2sql demo --no-browser` alongside
it and proxy or point `fetch` at `http://127.0.0.1:8765` to exercise the real
API; the app calls `/api/meta`, `/api/schema`, `/api/ask`, `/api/trace/{id}`
the settings routes (`GET /api/settings`, `POST /api/settings/key`,
`POST /api/settings/models`) and the index routes (`GET /api/index`,
`POST /api/index/rebuild`), the inspector's (`GET /api/retrieval`,
`POST /api/retrieval`) and feedback's (`GET /api/feedback`, `POST /api/feedback`).
The settings routes, Rebuild, the inspector and feedback refuse a request
whose `Origin` is not the page's own, so use them from the page `nl2sql demo`
serves, not from the Vite dev server.

## Scope

React and Vite only -- no router library (`src/router.js` is 110 lines over the
hash), no state library, no component kit, no CSS framework, no TypeScript. Plain JSX and plain CSS, kept small enough to read in
one sitting. Light and dark follow `prefers-color-scheme`; motion is limited to
the run arriving in order and is off under `prefers-reduced-motion`. The only
browser storage is the Debug toggle; settings live in the demo project's files,
and ratings in its schema store.
