# Schema Profiler

After a datasource is indexed, the profiler reads its live snapshot, asks the
database a bounded number of cheap questions through the adapter, and files
what it finds as [proposals](../configuration/review.md). It is deterministic:
no model runs unless you ask for drafted descriptions (`draft=True`, release
0.8, not in this change). Nothing it finds reaches a prompt, the index or a file
until a person with an admin role accepts it.

```bash
nl2sql index                        # the profiler reads the live build's snapshot
nl2sql profile --as admin           # file joins and column roles for review
nl2sql review list --as admin
nl2sql review accept --all --kind column --as admin   # writes glossary.yaml once
```

`nl2sql index --profile --as admin` indexes and then profiles the same
datasources. From Python:

```python
reports = engine.knowledge.profile(user_context=UserContext(roles=["admin"]))
```

`profile(datasource_ids=None, *, user_context, draft=False, max_cost_usd=None)`
returns one `ProfileReport` per datasource the caller reaches:

| Field | Meaning |
| --- | --- |
| `datasource_id`, `schema_version` | What was profiled; the proposals carry the same `schema_version` |
| `proposals` | Proposals created or updated, by kind (`join`, `column`) |
| `probes` | Overlap probes run |
| `skipped_by_budget` | Join candidates proposed without a probe because a bound was reached |
| `stopped_by` | `done`, `max_probes` or `time_budget` (`cost_cap` is for drafting) |
| `issues` | A probe that failed, an unavailable datasource, or "not indexed yet" |
| `seconds` | How long it took |

The caller is required and has no default. The profiler reads and probes only
the tables the caller's scope reaches, so a table the role denies is never
probed and is named in no proposal. A datasource that was never indexed is
reported with an issue and skipped: the profiler never reflects on its own.
Each finished profile publishes `profile.finished` (counts only).

## What it proposes

Nothing is proposed for what the glossary already says, what the database
already declares (a foreign key is not proposed as a join), or what the schema
proves. A primary or foreign key is an `identifier` and a temporal type a
`date`: those roles are *derived* when the knowledge is shown, with origin
`derived`, and never filed.

### Joins

For each column that is not part of a foreign key, the profiler looks for a
single-column primary key (or the only column named `id`) in another table that
it may point to. A pair of tables with a declared foreign key between them, or
a column pair the glossary already joins, is skipped.

1. **Names.** The column's name equals the key's (not when the key is a bare
   `id`), or it is the key table's name, singular or plural, followed by `id`
   or `key`. Case and underscores are ignored: `customer_id` finds
   `customers.id`, `CustomerId` finds `Customer.CustomerId`.
2. **Types.** Both columns fall in the same category: number, text, temporal
   or boolean. A mismatch drops the candidate, and a type the profiler cannot
   place never matches. This is stricter than the query-time join-key check,
   which coerces a number against text: a proposed join should hold without
   coercion.
3. **Overlap.** `probe_overlap` takes up to `profiler.sample_rows` distinct
   non-null values of the left column and counts how many exist in the key
   column. An overlap under `profiler.min_overlap` (0.9), or an empty sample,
   drops the candidate.
4. **Cardinality.** `many-to-one` when the key is a primary key, else `unknown`.

A join proposal's `change` is the glossary's join (`from`, `to`, `cardinality`)
and its `evidence` is the name rule that matched (`same_name` or
`table_name_key`), both types, the shared `category`, and `sampled`, `matched`
and `overlap` (counts, never values). A candidate the budget did not reach, and
one on an adapter without `probe_overlap` (the five built-ins have it), is
proposed with `overlap: null`. A probe that fails (a timeout, a driver error) is
an `issue` on the report, and its candidate is proposed with `overlap: null`
too. An accepted join is a relationship: the schema retriever lists it beside
the foreign keys, so the validator accepts a plan that uses it.

### Column roles

| Proposed | When |
| --- | --- |
| `enum` | A text or integer column, not an identifier and not named like a key (`..._id`), with at most `profiler.enum_max_distinct` (50) distinct values. `values` lists them with empty meanings for the reviewer to fill, only when the snapshot holds all of them (`index.sample_values` on) |
| `money` | A decimal column named like `price`, `amount`, `total`, `cost`, `revenue`, `salary`, `fee` or `balance`; `unit` from a `_usd`, `_eur` or `_gbp` suffix |
| `quantity` | An integer or decimal column named like `qty`, `quantity`, `count` or `units`, or with a `_kg`, `_ms` or `_pct` suffix, which is the `unit` |
| `date` on text | A text column whose smallest and largest values are ISO dates (SQLite keeps dates as text) |
| A refined `grain` | `month` or `year` when every value of a date column is the first of a month or of a year (needs all the column's values in the snapshot) |

A role, unit, grain or set of values the glossary already has is not proposed
again. The word lists are English only. The rules are one table in
`nl2sql/profiler/roles.py`, with a test per row. On a wide schema (every key and
temporal column is derived) this is a few hundred proposals at most, and
`nl2sql review accept --all --kind column --datasource ID --as admin` writes the
glossary once.

## Bounds

```yaml
# settings.yaml
profiler:
  sample_rows: 1000     # distinct values one overlap probe reads
  max_probes: 200       # overlap probes per datasource
  time_budget_s: 60     # per datasource; no probe starts after it
  min_overlap: 0.9
  enum_max_distinct: 50
  max_cost_usd: 0.50    # drafting, per profile call (with drafts)
```

Probes run one at a time, strongest candidate first (an exact key-name match
before a table-name match), each under the datasource's `timeout_ms`. The limit
caps the sample, not the read: `DISTINCT` usually reads the whole column first,
so on a large table the timeout is what bounds a probe. When `max_probes` or
`time_budget_s` is reached the remaining candidates are proposed with
`overlap: null` and the report says which bound stopped it.

## Proposals are idempotent

A proposal's id hashes its kind, datasource, target and change, so profiling
twice updates the same open rows' evidence rather than adding rows. A rejected
change is never proposed again, and an accepted one is in the glossary, which
the next profile reads.
