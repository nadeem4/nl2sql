# Jobs and Storage

How long work runs in the background and where an engine keeps its data, for operators sizing a deployment and contributors adding a store or a job.

The configuration is in [Storage](../configuration/storage.md); the job routes are in [Jobs API](../api/rest/jobs.md).

## Background jobs

Work that outlasts a request (indexing, profiling, an eval run, generating or building a dashboard, adding the demo databases) runs as a **job** in `nl2sql-api` (`packages/api/src/nl2sql_api/jobs.py`).

- A job is one facade call, wrapped in a runner that reports progress and watches a cancellation token. The API holds no feature logic of its own: everything a job does is a method any Python caller can call.
- Jobs run **in process, on one worker thread, in submission order**. The API runs one Uvicorn worker, so there is one queue.
- A caller with a job queued or running gets a 409 that names it. Cancelling a queued job removes it; cancelling a running one cancels its token, and it stops between items.
- The last 100 jobs are kept, **in memory**. A restart loses the list; what a job produced (an index build, a dashboard) is in the engine's stores.
- `GET /jobs/{id}/events` streams progress as Server-Sent Events from an async generator, with a heartbeat, so a waiting browser holds no worker thread.
- A job's `result` can hold rows read under its owner's scope, so only the owner sees it. An admin who views the job sees status, kind, progress and error.

The hosted demo allows each visitor one job and two open streams.

## What lives under `storage.path`

`storage` is a required section. An engine keeps everything it writes under one folder, with one slot per kind of data, and each slot is filled by a store named by `type`:

| Slot | Built-in store | Path | Holds |
| --- | --- | --- | --- |
| `index` | `chroma` | `<path>/index/` | the vector index: one namespace per embedding model, and each datasource's live build |
| `state` | `sqlite` | `<path>/state.db` | schema snapshots and diffs, the plan cache, feedback, review proposals, locks, conversations, workspaces, dashboards and their stored chart results, saved answers, approvals and API keys |
| `traces` | `files` | `<path>/traces/` | run traces |
| `results` | `local` | `<path>/results/` | result files and manifests |

Two more things sit beside them: the `audit` observer's day files in `<path>/audit/`, and in the hosted demo the session signing key `hosted_session.key`.

Store implementations are plugins in the `nl2sql.stores` entry point group, and the interfaces they implement are in `nl2sql_sdk.stores`. Outside the adapters, only `packages/nl2sql/src/nl2sql/storage/` imports `sqlite3`, and only it imports `chromadb` (the local embedding model borrows chromadb's embedding function, not its index).

## Why one folder

There is no hidden default for where data lives, and two engines with different `storage.path` values never share a store. Backing up or moving an installation is copying one folder; deleting it resets everything.

## Locks

The state store holds named locks (`conversation:<id>`, `dashboard-refresh:<id>`) so that one conversation runs one turn at a time and one dashboard refreshes once at a time, even across processes that share a state store.

## Retention

`engine.storage.purge()` deletes what has outlived its `max_age_days`: traces (14 days by default), result files (7), conversations and draft dashboards (30), decided review proposals, expired approvals and old audit files. Saved dashboards and saved answers are never purged. `nl2sql-api` runs it at startup and daily; `nl2sql storage purge` runs it by hand.

## The index commit

A build writes the new snapshot and index entries first and switches the datasource's build pointer last. Readers resolve "live" through the pointer, so a build that fails halfway is invisible and the next build deletes its leftovers. See [Indexing and retrieval](indexing.md).

## Where it is enforced

The `storage` section is required by `Config`, and `Runtime.from_config()` builds each slot's store with `folder=storage.path`. See [Invariants](invariants.md#an-engine-keeps-its-data-under-storagepath).
