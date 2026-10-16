# Schema Drift

A database changes after it is indexed: a column is added, a table dropped, a
type widened. The engine keeps answering from the schema its build was made
from until the next `nl2sql index`. Schema drift is **measured**, not acted
on: nothing here re-indexes, schedules a build or changes what a question
reads. You decide when to rebuild.

There are two measures:

- **A check**: the live database against its live build, now
  (`engine.index.check()`, `nl2sql index --check`).
- **The stored diffs**: what each build changed against the one it replaced
  (`engine.index.versions()`, `engine.index.diff()`).

## The check

```bash
nl2sql index --check                  # every datasource
nl2sql index --check -d shop --json   # one, as JSON
```

For each datasource it reflects the live database with the datasource's
`index` scope and no profiling (only the catalog is read), and compares its
structural fingerprint with the fingerprint of the snapshot the live build was
made from. It writes nothing.

| Exit code | Meaning |
| --- | --- |
| 0 | Nothing drifted (a datasource never indexed is reported and does not change the code) |
| 1 | At least one datasource drifted |
| 2 | At least one check failed (the database could not be read, or the config did not load), or nothing was checked (no datasource is configured, or the role reaches none); wins over 1 |

So a cron job or a CI step can act on it. `--role` (repeatable, default
`admin`, as `nl2sql run`) is the role the check runs as; drift shows only in
the tables it may read. A role that reaches no datasource checks nothing and
exits 2, so a gate never passes on a check that did not run. `--json` prints
the reports.

From code:

```python
from nl2sql import UserContext

for report in engine.index.check(user_context=UserContext(roles=["admin"])):
    print(report.datasource_id, report.status)
    if report.status == "drifted":
        print(report.diff.counts(), report.impact)
```

Each `DriftReport` has:

| Field | Meaning |
| --- | --- |
| `datasource_id` | The datasource |
| `status` | `unchanged`, `drifted`, `not_indexed` (no build yet) or `failed` |
| `live_version` | The live build's `schema_version` |
| `checked_at` | When (UTC) |
| `diff` | With `drifted`: a `SchemaDiff` from the live build (`from_version`) to the live database (`to_version` is `None`) |
| `impact` | With `drifted`: what the diff breaks (below) |
| `error` | With `failed`: why, redacted (a password in a connection string never shows) |

## The diff

`SchemaDiff` (`nl2sql_sdk.schema`, also exported from `nl2sql`) compares
exactly what the fingerprint covers:

| Field | Meaning |
| --- | --- |
| `tables_added`, `tables_dropped` | `TableRef`s. A table added or dropped is listed once, without its columns |
| `columns_added`, `columns_dropped` | `ColumnRef`s, in tables on both sides |
| `columns_changed` | `ColumnChange`: `table`, `column`, `changes` such as `{"type": ["INTEGER", "BIGINT"]}`; the keys are `type`, `nullable` and `pk` |
| `foreign_keys_added`, `foreign_keys_dropped` | `ForeignKeyRef`: `table`, `columns`, `referred_table`, `referred_columns` |
| `engine_type` | `(old, new)` when the datasource's engine type changed (its `type`), else `None` |

`diff.counts()` is the number of each, and `diff.is_empty()` is true exactly
when the two fingerprints are equal. Statistics, descriptions and sample
values are not structure: a build whose statistics alone changed has no diff.

**A rename is a drop and an add.** Nothing can tell a renamed column from a
dropped one and a new one without asking someone, so the diff does not guess.

## Stored diffs

When a build's new snapshot differs in structure from the version it
replaces, the diff between them is stored beside the new version in the state
store (see [Schema Store](store.md#schema-diffs)) and `schema.drifted` is
published with `detected_by: "build"`. It is pruned with its snapshot.

| Method | Returns |
| --- | --- |
| `engine.index.versions(datasource_id, *, user_context)` | Each stored version, newest first: `schema_version`, `fingerprint` (of the schema as the caller may see it), `live`, `has_diff` |
| `engine.index.diff(datasource_id, schema_version=None, *, user_context)` | The diff that introduced the version (the live one by default), or `None` |

Reading these is the measurement: how often a datasource's structure changes
between builds, and how far the live database is from the build at a check.

## Impact

A drifted report's `impact` (`DriftImpact`) lists what the diff breaks, from
data the engine already has:

| Field | What |
| --- | --- |
| `verified_queries` | Each verified query (`datasource_id`, `query_id`, `reasons`) whose SQL reads a dropped table, a dropped column, or a column whose type changed. A column is matched by its table (its qualifier's alias, or the only table read); an unqualified column in a query of several tables is reported as one it *may* read |
| `policy_rules` | Each role's `allow` or `deny` pattern (`role`, `kind`, `pattern`) that matched a dropped table and now matches nothing |
| `preferred_tables` | The datasource's `preferred_tables` that were dropped |
| `glossary_items` | Each [glossary](../configuration/knowledge.md) item (`tables.orders`, `columns.orders.note`, `terms.revenue`, `rules.pay_private`, `joins.a.id->b.id`) that named a dropped table or column, of those the caller may see |

The plan cache needs no entry: it is keyed on the schema version, so a
rebuild already makes its old plans unreachable.

## Who sees what

`user_context` is required on all three methods. A caller sees drift only in
the tables its scope may read:

- A table its roles deny is left out of the diff, and so is a foreign key
  that refers to one; the impact is computed from what is left, against the
  tables it may read.
- The `fingerprint` `versions()` shows is of the schema without the denied
  tables, so a change only to a denied table looks like a statistics-only
  rebuild.
- Drift only in denied tables is `unchanged` for that caller, and such a
  stored diff reads as `None` (`has_diff` false).
- A datasource it cannot reach is left out of `check()`; named, it is a
  `ValueError` worded exactly like an unknown datasource.

`schema.drifted` is an operator event: it is on the engine's bus
(`engine.events.subscribe`) and in the audit log, never on a caller's
`stream_query`. It carries the versions and the whole diff's counts, and no
table names. A check publishes a drift once: while the same drift of the same
build stays, later checks (by any caller) publish nothing, so a check every
few minutes writes one audit line per change. This is remembered in memory
per engine, so a new process publishes it once more. A build publishes each
diff it stores. See [Events](../observability/events.md).

## Source references

- The diff and what a caller sees of it: `packages/nl2sql/src/nl2sql/schema/diff.py`
- The check, versions and stored diffs: `packages/nl2sql/src/nl2sql/schema/drift.py`
- Impact: `packages/nl2sql/src/nl2sql/schema/impact.py`
- The models: `packages/sdk/src/nl2sql_sdk/schema.py`
- Storing a build's diff: `packages/nl2sql/src/nl2sql/indexing/orchestrator.py`
- Design: [Wave A, section 10](../architecture/wave_a.md#10-schema-drift-measure-first)
