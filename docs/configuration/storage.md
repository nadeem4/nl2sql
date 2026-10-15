# Storage

The `storage` section of [`nl2sql.yaml`](system.md) says where the engine keeps
its data, and which store keeps each part of it. Only `path` is required:

```yaml
# storage.yaml
path: .nl2sql          # or "${env:NL2SQL_DATA_DIR}" in a container
```

A relative `path` is relative to `nl2sql.yaml` in a file, and to the working
directory when the `Config` is built in code. `nl2sql init` writes `.nl2sql`
and adds `.nl2sql/` to `.gitignore`.

Everything the engine writes lives under it:

| Under `path` | What | Was (0.4) |
| --- | --- | --- |
| `index/` | The vector index (Chroma), one collection per embedding model: `nl2sql__<model slug>__<dimension>`. | `VECTOR_STORE`, `VECTOR_STORE_COLLECTION` |
| `state.db` | SQLite: schema snapshots, the plan cache and playground feedback. | `SCHEMA_STORE_PATH` |
| `traces/` | Run traces (`settings.traces.mode`), kept 14 days by default. | `TRACE_DIR` |
| `results/` | Result files, `<tenant_id>/<request_id>/<sub_query_id>.parquet`. Kept 7 days by default. See [Artifact Store](../storage/artifact-store.md). | `RESULT_ARTIFACT_BASE_URI` |

## The four stores

Each part of that data has a slot, and each slot a store, named by `type`.
Every sub-block is optional; left out, a slot uses its built-in store, so the
section above is the same as:

```yaml
path: .nl2sql
index:   { type: chroma }    # <path>/index/
state:   { type: sqlite }    # <path>/state.db
traces:  { type: files, max_age_days: 14 }    # <path>/traces/
results: { type: local, max_age_days: 7 }     # <path>/results/
```

| Slot | Built-in | Holds |
| --- | --- | --- |
| `index` | `chroma` | The vector index, one namespace per embedding model, and each datasource's live build |
| `state` | `sqlite` | Schema snapshots, the plan cache, feedback |
| `traces` | `files` | Run traces |
| `results` | `local` | Result files |

A store's other keys are its own, checked by the store when the engine is
built, so a misspelt key is an error. They may be `${provider:key}`
references, resolved through the [secret providers](secrets.md) when the store
is built:

```yaml
index: { type: qdrant, url: "http://qdrant:6333", api_key: "${env:QDRANT_KEY}" }
```

Store names share one entry point group, `nl2sql.stores`, so a name is unique
across the four slots. A store in the wrong slot is refused with the stores
installed for that slot: "'chroma' is an index store; results needs a results
store. Installed: local."

In code, a slot takes a `StoreConfig` or a store object, used as given:

```python
from nl2sql import Storage, StoreConfig

Storage(path=".nl2sql")
Storage(path=".nl2sql", traces=StoreConfig(type="files", max_age_days=30))
Storage(path=".nl2sql", index=my_index_store)   # any object whose slot is "index"
```

The store interfaces are in [`nl2sql_sdk.stores`](../adapters/sdk.md#interfaces-the-stores).
A plugin registers a store in the `nl2sql.stores` entry point group, or in
code with `registry.add_store(name, cls)`.

Every slot is read through its interface, so a plugin store works in any slot it fills.

## Retention

The `files` trace store and the `local` results store take `max_age_days`,
14 and 7 by default (at least 1). It is a key of those two stores, not of
every store.

```yaml
traces:  { type: files, max_age_days: 30 }
results: { type: local, max_age_days: 2 }
```

`engine.storage.purge()` deletes the traces and result files last written
before that age, and the [audit observer's](observers.md#audit) day files older
than its `max_age_days` (90 by default), and returns the counts,
`{"traces": 3, "results": 12, "audit": 1}` (no `audit` when the engine runs no
audit observer). It publishes the counts as the
[`retention.purged` event](../observability/events.md#the-catalog), which the
audit observer writes. A
plugin trace or results store is purged when it has a `max_age_days`
attribute, and left alone when it does not (a bucket with its own lifecycle
rule, say). Three things call it:

- `nl2sql storage purge`, by hand or from cron;
- `nl2sql-api`, at startup and then once a day while it serves (a failed
  purge is logged and tried again the next day);
- your own code.

Feedback and schema snapshots have no age limit: feedback is cleared by hand
(`nl2sql feedback clear`), and after each index build the snapshots nothing can
read any more are deleted (see [The state](#the-state)).

## What is where: `engine.storage.info()`

```python
engine.storage.info()
# {"index":   {"type": "chroma", "location": ".../.nl2sql/index"},
#  "state":   {"type": "sqlite", "location": ".../.nl2sql/state.db"},
#  "traces":  {"type": "files",  "location": ".../.nl2sql/traces", "max_age_days": 14},
#  "results": {"type": "local",  "location": ".../.nl2sql/results", "max_age_days": 7},
#  "namespace": "nl2sql__local-all-minilm-l6-v2__384"}
```

`type` is the store's name, or the class name of a store object; `location`
is its folder or file, or `None` for a store that does not have one.

## The state

The state store keeps the schema snapshots (immutable, one row per version),
the plan cache, the playground's feedback, and the lock that lets one index
build run at a time. The engine applies the store's pending migrations when it
starts; the built-in `sqlite` store adopts a 0.5 `state.db` as it is. See
[The state store](../schema/store.md#the-state-store).

## The index

The engine embeds; the index store keeps entries and vectors in a
**namespace** per embedding model, `nl2sql__<model slug>__<dimension>` (for
example `nl2sql__local-all-minilm-l6-v2__384`), filters them, and records
each datasource's live build as a `BuildPointer` (its `build_id` and the
`schema_version` it was made from). Switching a pointer is the commit, and a
run reads the pointers once when it starts. See
[Indexing](../architecture/indexing.md#the-build-pointer-is-the-commit).

- **Changing the embedding model** opens a new, empty namespace: `check()`
  reports the index `missing`, and `nl2sql index` builds it. Switching back
  finds the old namespace still built.
- **`nl2sql index prune`** (`engine.index.prune()`) drops every namespace but
  the current model's, including 0.5's `nl2sql_store` collection, which 0.6
  never reads. Run it once after upgrading, and after settling on a new model.
- The `chroma` store opens `index/` on the first build; reading a missing
  folder or namespace creates nothing.

## Sharing

Two engines with different paths share nothing. The commands that only read
these stores (`nl2sql cache clear`, `nl2sql feedback`, `nl2sql trace show`,
`nl2sql storage purge`) find them from the same config, so run them with the
same `--config`.

There is no built-in S3 or ADLS results store. The `ResultStore` interface is
four methods; [Artifact Store](../storage/artifact-store.md#a-results-store-of-your-own)
has an S3 example.

In a container, mount a volume and point the path at it:

```yaml
path: "${env:NL2SQL_DATA_DIR}"
```

```bash
docker run -e NL2SQL_DATA_DIR=/data -v nl2sql-data:/data ...
```
