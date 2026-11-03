# Status and limitations

The known gaps in the released packages, for anyone deciding whether NL2SQL fits their use.

The packages are pre-1.0. `nl2sql-sdk`, `nl2sql-engine`,
`nl2sql-api` and `nl2sql-cli` share one version and are released together
([Releasing](../contribute/releasing.md)). Unreleased changes are on `main`;
see the [changelog](https://github.com/nadeem4/nl2sql/blob/main/CHANGELOG.md) for what is released.

Known limitations:

- Every question needs an LLM; only indexing is key-free. The key-free demo
  answers nothing until you record answers or add a key.
- Small local models often fail to produce a valid plan.
- A question cannot span two databases: every sub-query is planned against one
  datasource, so the demo's three cannot be joined, however much their data
  lines up.
- Read-only is enforced by SQLite when a datasource asks for it, and by nothing
  else (see [Security Model](../security/model.md)), so a database user that can only read is still the real
  control. `max_bytes` is not enforced; `row_limit` is, in the generated SQL.
- The hosted demo's rate limits are best-effort: they live in one process, and
  the per-session cap hangs on a cookie the visitor can clear.
- The model's output is not reproducible; determinism is structural (fixed
  topology, validation before generation, plan cache) ([Determinism](../how-it-works/determinism.md)).
- The engine does not authenticate callers in the query path: it believes the
  `UserContext` it is given. The REST API resolves one from an API key, a
  trusted proxy's headers, a static role or the local operator
  ([Security Model](../security/model.md)). `run_query` without a
  `user_context` raises instead of refusing.
- OpenTelemetry metrics and traces are off unless an `otel` observer is
  configured ([Observability](../observability/stack.md)).
- `nl2sql launch` ([Launch](../get-started/launch.md)) walks you from a
  database to a working project, but it does not cover every datasource type
  or every setting.
- The `metrics:` list in `curated.yaml` is parsed and validated, but no
  question is answered from it yet; only verified queries are used
  ([Curated answers](../how-it-works/curated_answers.md)).
- A verified query answers a whole question: one part of a multi-part question
  cannot be answered from a verified query while the rest is generated.
