# Storage

The `storage` section of [`nl2sql.yaml`](system.md) says where the engine keeps
its data. It has one key:

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
| `index/` | The vector index (Chroma). The collection name is an internal constant. | `VECTOR_STORE`, `VECTOR_STORE_COLLECTION` |
| `state.db` | SQLite: schema snapshots, the plan cache and playground feedback. | `SCHEMA_STORE_PATH` |
| `traces/` | Run traces (`settings.traces.mode`). | `TRACE_DIR` |
| `results/` | Result files, `<tenant_id>/<request_id>/<sub_query_id>.parquet`. See [Artifact Store](../storage/artifact-store.md). | `RESULT_ARTIFACT_BASE_URI` |

Two engines with different paths share nothing. The commands that only read
these stores (`nl2sql cache clear`, `nl2sql feedback`, `nl2sql trace show`)
find them from the same config, so run them with the same `--config`.

Results are local files. The S3 and ADLS result backends of 0.4 were removed;
remote stores come back with the results-store interface in a later release.

In a container, mount a volume and point the path at it:

```yaml
path: "${env:NL2SQL_DATA_DIR}"
```

```bash
docker run -e NL2SQL_DATA_DIR=/data -v nl2sql-data:/data ...
```
