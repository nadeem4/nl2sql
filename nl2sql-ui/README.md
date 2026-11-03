# nl2sql-ui

The source of the NL2SQL web app: React and Vite, plain JSX and plain CSS, with no router library, state library, component kit or TypeScript. This README is for people changing that code. To use the app, read [Web app](../docs/apps/web.md).

## How it is built and served

`npm run build` writes `index.html` and hashed chunks into `packages/api/src/nl2sql_api/web/static/` (see `vite.config.js`). That folder is not committed. `nl2sql-api` serves the build and the API wheel and image carry it, so users need no Node toolchain.

The page calls the REST API under `/api/v1` and nothing else, through `src/api/http.js` (JSON and Server-Sent Events). Pages are hash routes (`#/chat`, `#/dashboards`, ...) defined in `src/router.js`.

## Commands

Run these in `nl2sql-ui/`:

```bash
npm ci          # install
npm run dev     # Vite dev server; it has no API proxy
npm run build   # build into the API package
npm test        # node --test "src/**/*.test.js"
```

The dev server serves the page only. To exercise the real API, run `npm run build` and start `nl2sql-api`, or run `nl2sql demo` from `nl2sql-cli[web]`, which starts it for you.

## Source layout

| Folder | What is in it |
| --- | --- |
| `src/api/` | One client file per API area, with `http.js` underneath. `fixtures/` holds sample responses for tests. |
| `src/pages/` | One file per route, plus the small helpers each page needs. |
| `src/chat/` | The conversation: turns, the run spine, clarification, approval, feedback, the why panel. |
| `src/answers/` | My answers. |
| `src/dashboards/` | Workspaces, the builder, generate, schedule, filters and tiles. |
| `src/outputs/` | What a result or tool output renders as: tables, charts, reports, diagrams. |
| `src/knowledge/` | Glossary, proposals and drift. |
| `src/connect/` | The Connect page's steps and schema form. |
| `src/evals/` | Reading an eval report. |
| `src/components/` | Pieces shared by pages (`JobProgress`, `SqlCard`, `Ledger`, `NodeInspector`). |
| `src/lib/` | Pure helpers: settings, hosted keys, run state, SQL highlighting. |
| `src/styles/` | The stylesheet, split by area and listed in cascade order in `index.css`. |

Logic lives in plain `.js` files next to the `.jsx` that uses it, and most have a `*.test.js` beside them, so tests run under Node without a browser.

## Related

- [Web app](../docs/apps/web.md): what each page does, for users.
- [REST API](../docs/api/index.md): the routes the page calls.
- [Contributing](../CONTRIBUTING.md): how CI runs the web tests.
