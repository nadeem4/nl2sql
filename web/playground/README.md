# nl2sql playground UI

The React source for the page `nl2sql demo` serves.

## What the page shows

- **Database** (left rail, or below the run on a narrow window): the indexed
  schema from `/api/schema`, visible before any question. Each table shows its
  row count and the tables it refers to; open one for columns, types, keys and
  foreign keys. Tables the current plan reads are marked `in plan`; tables the
  role was refused are marked `refused for <role>`.
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

Every station renders the `/api/ask` response; nothing is computed server-side
for the page.

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
ledger, refused-table parsing) with Node's built-in test runner; there is no
test dependency.

## Fonts and offline use

The page makes no network request other than the four API routes: no CDN, no
Google Fonts. The two typefaces are bundled at build time from `@fontsource`
(dev dependencies), Latin subset only, and inlined into the page:

- **Schibsted Grotesk** (variable) for the interface;
- **Fragment Mono** for plans, SQL, table and column names, and numbers.

Both are SIL Open Font License. The built page is about 275 KB.

## Develop

```bash
npm run dev
```

Vite serves the page on its own port. Run `nl2sql demo --no-browser` alongside
it and proxy or point `fetch` at `http://127.0.0.1:8765` to exercise the real
API; the app only calls `/api/meta`, `/api/schema` and `/api/ask`.

## Scope

React and Vite only -- no router, no state library, no component kit, no CSS
framework, no TypeScript. Plain JSX and plain CSS, kept small enough to read in
one sitting. Light and dark follow `prefers-color-scheme`; motion is limited to
the run arriving in order and is off under `prefers-reduced-motion`.
