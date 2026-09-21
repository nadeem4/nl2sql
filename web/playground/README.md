# nl2sql playground UI

The React source for the page `nl2sql demo` serves.

## What the page shows

- **Database** (left rail, or below the run on a narrow window): the indexed
  schema from `/api/schema`, visible before any question. Each table shows its
  row count and the tables it refers to; open one for columns, types, keys and
  foreign keys. Tables the current plan reads are marked `in plan`; tables the
  role was refused are marked `refused for <role>`.
- **Search index** (`#index-panel`, top of the rail): from `GET /api/index`,
  the vector index the resolver searches, which the Database below does not
  show. A status line (`#index-status`), entries by type (`#index-counts`), the
  schema version (`#index-version`) and when it was built (`#index-built`).
  When the index is empty, missing or out of date, the panel turns to the fault
  colour, a stale index lists why, and a warning under the top bar
  (`#index-warning`) links to the button. **Rebuild** (`#index-rebuild`) is
  always offered; it posts to `POST /api/index/rebuild` and the page polls
  `GET /api/index` for its steps (`#index-progress`) until it ends, then
  re-reads the schema. **Write descriptions with the LLM** (`#index-enrich`) is
  off by default and disabled without a key. A failure shows `#index-error`.
  Where Rebuild is off (a non-loopback `--host` without `--allow-settings`) the
  panel says why (`#index-unavailable`). A demo folder written by an older
  engine shows `#index-folder-warning`.
- **Composer**: the question box, the role selector (`#role-select`), **Plan
  only** (`#plan-only`), **Debug** (`#debug-toggle`) and the guided questions
  from `/api/meta`.
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
  under it and its time includes theirs. Debug is on by default; the choice is
  kept in `localStorage` (`nl2sql.playground.debug`) and the page works the same
  when storage is unavailable.
- **Node drill-down**: when the run wrote a trace (`trace_path` in the
  response; the demo writes one for every run), each node name in the Debug
  table is a button. It fetches the trace once from `GET /api/trace/{trace_id}`
  and shows that node's executions (attempts and sub-queries) with errors and
  warnings, the state it read, the update it returned and, for LLM nodes, the
  exact prompt, the raw response and the parsed result, each folded. A
  **Download trace** link sits above the table.

- **Settings** (`#settings-toggle`, top right; the panel is `#settings-panel`):
  shut until asked for, and it pushes the page down rather than covering the
  run. **API key** (`#settings-key`, `#settings-save-key`) shows the key in use
  only in masked form (`#settings-key-current`); saving one writes it to the
  demo project's `.env.demo` and turns replay into live without a restart, and
  the mode line follows. **Model for each step** has one selector per LLM node
  (`#model-decomposer`, `#model-astplanner`, `#model-refiner`,
  `#model-answersynthesizer`) with a Default option, and **Save models**
  (`#settings-save-models`) writes the changed ones to `configs/llm.demo.yaml`.
  Choosing a model that runs without a temperature shows
  `#settings-temperature-note`. The model list comes from `GET /api/settings`;
  the page names no model itself. When the server has settings off (a
  non-loopback `--host` without `--allow-settings`) the panel shows the reason
  (`#settings-unavailable`) instead of a form.

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
temperature) and `src/indexHealth.js` (entry counts in plain words, the status
line, relative build times) with Node's built-in test runner; there is no test dependency.

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
`POST /api/index/rebuild`). The settings routes and Rebuild refuse a request
whose `Origin` is not the page's own, so use them from the page `nl2sql demo`
serves, not from the Vite dev server.

## Scope

React and Vite only -- no router, no state library, no component kit, no CSS
framework, no TypeScript. Plain JSX and plain CSS, kept small enough to read in
one sitting. Light and dark follow `prefers-color-scheme`; motion is limited to
the run arriving in order and is off under `prefers-reduced-motion`. The only
browser storage is the Debug toggle; settings live in the demo project's files.
