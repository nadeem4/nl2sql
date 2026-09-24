# Working in this repo

Hard rules, with the reason in one line each. The reasoning, the examples and the
enforcement points are in [`docs/architecture/invariants.md`](docs/architecture/invariants.md);
don't restate them here. Most of what follows is enforced by
`packages/nl2sql/tests/architecture/test_boundaries.py`, whose failure messages
name the rule.

## Package boundaries

- **The adapter SDK imports only pydantic and the standard library.** It is what a third-party adapter compiles against, so anything it imports, every adapter author inherits.
- **An adapter imports only the SDK, its driver, SQLAlchemy, sqlglot, pydantic and the standard library.** An adapter that imported the engine would close the circle and make the split meaningless.
- **Nothing outside `nl2sql/adapters/` imports an adapter, SQLAlchemy or a database driver.** Adapters are reached through the `nl2sql.adapters` entry points and `DatasourceAdapterProtocol`, so a datasource this repo has never heard of works the same way.
- **Nothing outside `nl2sql/cli/` imports the CLI**, and `cli.demo` never imports `cli.commands`. Knowledge an SDK or REST caller also needs belongs in the engine, not behind a command.
- **The REST API imports only the top-level `nl2sql` facade, and the playground never touches `engine.context`.** Whatever the playground needs is a facade method `nl2sql-api` can call too.
- **The runtime never imports `nl2sql.evaluation` or `nl2sql.feedback` at module scope.** Both are opt-in tools; a lazy import inside a function is fine.
- **A provider's API-key environment variable is read in `nl2sql/llm/` only.** `PROVIDER_PRESETS` in `llm/registry.py` is the one table; everything else derives from it.

## The plan, the adapter, and SQL

- **No dialect name and no dialect-specific SQL outside `nl2sql/adapters/`** — not in a prompt, not in an error message. The plan says *what*; the adapter says *how*.
- **`get_dialect()` returns a sqlglot dialect name** (`postgres`, `tsql`), not SQLAlchemy's. It is fed straight to `sqlglot.Dialect.get_or_raise`.
- **The model emits a typed plan, never SQL**, and never a function name outside `ALLOWED_FUNCTIONS` (`pipeline/nodes/ast_planner/functions.py`). `func_name` is model text that becomes the function's name in the query.
- **Validation before generation:** anything the generator can reject, the logical validator must reject first. The validator is the last node with a retry edge, so a check that lives only in the generator is a dead end rather than a retry.

## How to change things

- **TDD.** Write the failing test first; it is how a rule stops being a memo.
- **Update the docs in the same PR as the code.** A doc that lags is worse than no doc — see `CONTRIBUTING.md`.
- **Never read or edit `nl2sql-demo*/` or any `.env` file.** A folder made by `nl2sql demo` holds a real API key in `.env.demo`; `.gitignore` keeps it out of the repo and you keep it out of the transcript.
- **Never merge the release-please PR (#85).** Merging it releases all three packages; releasing is a deliberate act, described in `docs/development/releasing.md`.
- **Where we control the background, Mermaid diagrams are black and white** — an artifact, a Notion page, paper — and open with exactly this line, so the diagram reads the same in all three:

    ```
    %%{init: {'theme':'base','themeVariables':{'primaryColor':'#ffffff','primaryTextColor':'#000000','primaryBorderColor':'#000000','lineColor':'#000000'}}}%%
    ```

- **Where the reader picks the theme, force no colours at all** — the README and anything else GitHub renders, and the docs site, which has a dark palette. That line paints black lines and black label text onto a near-black canvas; the default theme follows the reader instead. Carry emphasis in shape and layout.

- **Never change gold data to make a benchmark run pass.** Add a reviewed `alt_gold_sql` alternative instead: [`docs/testing/evaluation-dataset.md`](docs/testing/evaluation-dataset.md).

## Checks

```bash
pytest -m "not integration" -q
nl2sql setup --demo   # or copy packages/nl2sql/src/nl2sql/datasets/*.sqlite into data/
EMBEDDING_PROVIDER=local pytest -m "integration and not llm" -q
mkdocs build --strict
```

Integration tests skip loudly without the demo databases, so a green run that
skipped them proves nothing.
