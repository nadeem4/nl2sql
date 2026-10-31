# Datasource API

## Purpose
`engine.datasources`: the engine's datasources, listed with their status,
added and removed at runtime, tested, and their indexed schema; and the
methods behind Connect a database: the adapter types with their connection
forms, and a test of a datasource before it is added.

## Responsibilities
- List every configured datasource with its status, capabilities and masked connection.
- List every adapter type the registry can name, installed or not, with its connection model's JSON Schema.
- Test a configured datasource, as `check()` does, or a `Datasource` that is not added, registering nothing.
- Find databases to connect: scan a URL, a folder and the local ports for what the adapters declare they open.
- Add a `Datasource` (validated, built, not connected) and remove one.
- Save one datasource to its `datasources/<id>.yaml`, secrets as references, refusing a file changed since it was loaded.
- Return the indexed schema as the planner is given it, as one caller may see it.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/datasource_api.py` (`DatasourcesAPI`, internal)
- `packages/nl2sql/src/nl2sql/api/types.py` (`DatasourceInfo`, `DatasourceType`, `Candidate`)
- `packages/nl2sql/src/nl2sql/datasources/discovery.py` (`scan`)
- `packages/nl2sql/src/nl2sql/datasources/registry.py`
- `packages/nl2sql/src/nl2sql/config/models.py` (`Datasource`)
- `packages/nl2sql/src/nl2sql/config/writer.py` (secrets as references, `write_env_values`, the atomic write) and
  `config/project.py` (`write_project`, `starter_config`)

## Public Surface

| method | returns | meaning |
| --- | --- | --- |
| `list(*, user_context=None)` | `list[DatasourceInfo]` | Every configured datasource, in config order, failed ones included. With `user_context`, only the datasources its roles reach (`policies.scope`), so a caller never sees the sample questions of one it may not use. |
| `get(datasource_id, *, user_context=None)` | `DatasourceInfo` | One configured datasource. Raises `ValueError` for an unknown id, or one `user_context` does not reach. |
| `types()` | `list[DatasourceType]` | Every adapter the engine's registry can name, sorted by name, whether its driver is installed or not. An adapter module imports without its driver, so the connection schema is there before the driver is. A plugin that fails to import, or implements another interface version, is left out. |
| `test(datasource)` | `DatasourceStatus` | See [Testing a datasource](#testing-a-datasource). |
| `scan(folder=".", *, url=None, ports=True)` | `list[Candidate]` | The databases found, as connection candidates, in this order and each once: the `url` (its scheme, up to any `+driver`, picks the adapter), database files under `folder` (suffixes the adapters declare; two folders down, at most 50, no hidden folder, not `storage.path`), and with `ports` the adapters' default ports on `127.0.0.1` only (0.2 s each). Every adapter in the registry takes part, installed or not, if it declares [`DiscoverableAdapter`](../../adapters/sdk.md#optional-discoverableadapter). Opens no database session and builds no adapter; `test()` connects. `Candidate.connection` carries the URL's password: `repr` and `str` mask it, `model_dump` and `model_dump_json` do not, so never log or serialise a Candidate. `found.value` of a URL has the password masked and its query and fragment dropped. |
| `add(datasource)` | `None` | Validates `connection` against the adapter's connection model and `index` as an `IndexScope`, builds the adapter without connecting, registers it, and adds the `Datasource` to `engine.config`; its description and sample questions are what indexing reads. Raises `ValueError` if the id is taken, the type is unknown, a reference does not resolve, `index` does not validate (naming the `index.` field) or the adapter rejects its connection settings (`ConnectionInvalid`, a `ValueError` naming the field, with "did you mean" for an unknown key, and never a value; its `problems` list each as `{path, message}`). Index it next: `engine.index.build([datasource.id])`. |
| `add_demo(*, user_context=None, on_datasource=None)` | `RebuildResult` | Try the demo: adds the three sample databases (read-only, from the installed package), their verified queries and the demo glossary (copied to `demo-glossary.yaml` in the project folder) as ordinary datasources and knowledge, skipping any already configured (a demo id that is another database raises `DatasourceExists` and adds nothing), saving each added datasource under `datasources/` when the engine was loaded from a config, then runs `index.build` over them, passing `on_datasource` (an `IndexProgress` per datasource). With `user_context` the caller needs an admin role (`NotAuthorized`). Also `engine.add_demo(...)`. |
| `remove(datasource_id)` | `None` | Takes the datasource out of routing and out of `engine.config`, and closes its adapter's pool. Its index entries stay until the next full `index.build` or `index.clear`. Raises `ValueError` for an unknown id. |
| `save(datasource_id, *, by, env_file=False)` | `Path` | One datasource as `datasources/<id>.yaml` beside the `nl2sql.yaml` the engine was loaded from; returns the file. `by` must hold an admin role (`TypeError` if it is not a `UserContext`, `PermissionError` without an admin role). Every secret field (by its key, or declared secret by the adapter's connection model) is written as a reference: a literal needs `env_file=True` (it goes to the `.env` as `${env:NL2SQL_<ID>_<KEY>}`) and is otherwise refused with `SecretValueInConfig`. Refuses (`ReviewError`, code `FILE_CHANGED`, fix "reload the engine") a file that changed on disk since the config was loaded, and a new file that already exists. Raises `ValueError` for an unknown id, an engine built in code or whose `datasources` is not a folder include, an id that cannot be a file name (letters, digits, `_`, `-`, `.` only; no Windows device name), and two secrets that map to one environment variable. Written to a temporary file and renamed; publishes `datasource.saved` (audited). See [Saving a datasource](../../configuration/datasources.md#saving-a-datasource). |
| `schema(datasource_id, *, user_context)` | `dict` | The indexed schema as the planner is given it, as `user_context` may see it: `{"datasource_id", "tables": [...]}`, tables sorted by name, each with `name`, `schema`, `row_count`, `description`, `columns` (`name`, `type`, `nullable`, `primary_key`, `description`) and `foreign_keys` (`columns`, `references_table`, `references_columns`). A table the caller's roles deny is left out, and so is a foreign key that refers to one; a caller whose roles do not reach the datasource gets no tables. `user_context` is required: there is no default view, so code that forgot it fails instead of showing everything. Pass an admin's `UserContext` for the whole schema. Read from the snapshot the datasource's live index build was made from, not the database; before the first index `tables` is empty. |
| `dialect(datasource_id)` | `str` | The sqlglot dialect name of the datasource's adapter (`postgres`, `tsql`...). Used to read a verified query's SQL for [`diagram_for`](../../charts/diagrams.md#the-answer-view). |
| `diagram(datasource_id, *, user_context, tables=None)` | `str` | The schema as a Mermaid `erDiagram`, drawn from `schema(...)`, so only what `user_context` may see: a denied table, and every foreign key or glossary join that names one, is left out. Each table shows its primary and foreign key columns; each foreign key and each glossary join is a relationship. `tables` draws those tables and the ones one relationship away (a `ValueError` if none is a table the caller may see); without it, the whole datasource, cut to the 30 tables with the most relationships and a `%% N more tables not shown` comment. No colour, no `%%{init}` line, no model call. `user_context` is required, as for `schema`. See [Diagrams](../../charts/diagrams.md). |

`DatasourceInfo` is a frozen dataclass, exported from `nl2sql`:

| field | meaning |
| --- | --- |
| `id` | The datasource id. |
| `type` | Its `type` (the adapter's engine type for an adapter object). |
| `description` | Its `description`, or `None`. |
| `status` | `ready` once `check()` or `test()` connected to it, `failed` when one did not, `unchecked` before either ran. |
| `capabilities` | The capabilities its adapter declares, sorted. Empty for a failed datasource: nothing is routed to it. |
| `connection` | The configured `connection`, with every secret field masked as `***`. References (`${env:...}`) are shown masked too. |
| `reason` | Why it failed, or `None`. |
| `sample_questions` | The datasource's own `sample_questions`, then the accepted glossary `examples` not already there. Not scoped to a caller. |

`DatasourceType` is a frozen dataclass, exported from `nl2sql`:

| field | meaning |
| --- | --- |
| `name` | The name a datasource's `type` uses: `postgres`. |
| `title` | The adapter's title, for a form: `PostgreSQL`. |
| `installed` | Every one of its driver modules is found. |
| `install_hint` | What to install when it is not: `pip install "nl2sql-engine[postgres]"`. |
| `connection_schema` | The JSON Schema of its connection model (`connection_model.model_json_schema()`): the form. A secret field is `"format": "password", "writeOnly": true`, which a form renders as a password input; a field with `"group": "advanced"` is folded away. |

## Testing a datasource

`test()` takes a configured datasource's id or a `Datasource`.

- **An id** connects to that configured datasource, as `check()` does: `ready`
  puts it back in routing, `failed` keeps it out. Raises `ValueError` for an
  unknown id.
- **A `Datasource`** that is not added: builds its adapter, runs
  `test_connection`, closes the adapter, and registers nothing. It is what a
  Connect a database form calls before `add`. A failure is reported, never
  raised. An `adapter` object is tested as given and not closed: the caller
  owns it. The datasource's secret literals, and every secret the engine knows, are taken out of the `reason`
  and `fix` (`***`): a driver may echo the password it was given, and a form shows these words.

A failed status carries the stage, the reason and the fix:

| `stage` | When | `fix` |
| --- | --- | --- |
| `config` | The `type` is unknown (with the closest name), or `connection` does not validate against the adapter's model (the field, and "did you mean" for an unknown key; never a value) | What to correct |
| `driver` | A module in the adapter's `driver_modules` is not installed | The adapter's `install_hint` |
| the adapter's stage | `test_connection` failed: the `ConnectionCheck`'s `stage` (`network`, `auth`, `database`, `permission`), or `connect` where the adapter does not say | The `ConnectionCheck`'s `fix`, or a generic one |

```python
from nl2sql import Datasource

{t.name: t.installed for t in engine.datasources.types()}   # {'duckdb': True, 'mssql': False, ...}
form = next(t for t in engine.datasources.types() if t.name == "postgres").connection_schema

candidate = Datasource(id="sales", type="postgres",
                       connection={"host": "db.internal", "database": "sales", "user": "nl2sql",
                                   "password": "${env:SALES_DB_PASSWORD}"})
status = engine.datasources.test(candidate)   # registers nothing
if status.status == "ready":
    engine.datasources.add(candidate)
else:
    print(status.stage, status.reason, status.fix)
```

```python
from nl2sql import NL2SQL, Datasource, UserContext

engine = NL2SQL.from_config("nl2sql.yaml")
engine.check()
[(d.id, d.status) for d in engine.datasources.list()]   # [('shop', 'ready'), ('hr', 'failed')]
engine.datasources.get("hr").reason
engine.datasources.schema("shop", user_context=UserContext(roles=["analyst"]))["tables"]

engine.datasources.add(Datasource(id="ledger", type="sqlite", connection={"database": "ledger.db"}))
engine.datasources.test("ledger").status                 # 'ready'
engine.index.build(["ledger"])
```

To add every datasource of another `nl2sql.yaml`:

```python
from nl2sql import load_config

for datasource in load_config("other/nl2sql.yaml").datasources:
    engine.datasources.add(datasource)
```

See [Datasources](../../configuration/datasources.md) for the `Datasource`
fields, and [When a datasource fails to start](../../configuration/datasources.md#when-a-datasource-fails-to-start)
for running degraded.
