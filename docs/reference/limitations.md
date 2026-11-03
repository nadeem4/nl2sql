# Status and limitations

The engine is at **0.1.x**. `nl2sql-sdk`, `nl2sql-engine` and
`nl2sql-api` share one version and are released together
([Releasing](../contribute/releasing.md)). Unreleased changes are on `main`;
see the [changelog](https://github.com/nadeem4/nl2sql/blob/main/CHANGELOG.md) for what is released.

Known limitations:

- Every question needs an LLM; only indexing is key-free. The key-free demo
  answers nothing until you record answers or add a key.
- Small local models often fail to produce a valid plan.
- A question cannot span two databases: every sub-query is planned against one
  datasource, so the demo's three cannot be joined, however much their data
  lines up.
- There is no conversation: each question is answered on its own, with no memory
  of the last one, so "and by country?" is not a follow-up.
- Read-only is enforced by SQLite when a datasource asks for it, and by nothing
  else (see above), so a database user that can only read is still the real
  control. `max_bytes` is not enforced; `row_limit` is, in the generated SQL.
- The hosted demo's rate limits are best-effort: they live in one process, and
  the per-session cap hangs on a cookie the visitor can clear.
- The model's output is not reproducible; determinism is structural (fixed
  topology, validation before generation, plan cache) ([Determinism](../architecture/determinism.md)).
- No distributed tracing: OpenTelemetry metrics only, off by default. Audit
  events are emitted only by the CLI.
- The REST API does not authenticate callers: it takes the role from a trusted
  proxy header or a static setting. `run_query` without a `user_context` raises
  instead of refusing.

Roadmap: using the engine on your own database (bring-your-own-database setup
beyond the bundled demo databases) is planned. `nl2sql init` and the adapters
exist, but the guided connect-scan-review flow arrives in a later release.
