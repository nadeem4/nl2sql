# Datasource API

## Purpose
`engine.datasources`: the engine's datasources, listed with their status,
added and removed at runtime, tested, and their indexed schema.

## Responsibilities
- List every configured datasource with its status, capabilities and masked connection.
- Add a `Datasource` (built, not connected) and remove one.
- Connect to one datasource on demand, as `check()` does.
- Return the indexed schema as the planner is given it.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/datasource_api.py` (`DatasourcesAPI`, internal)
- `packages/nl2sql/src/nl2sql/api/types.py` (`DatasourceInfo`)
- `packages/nl2sql/src/nl2sql/datasources/registry.py`
- `packages/nl2sql/src/nl2sql/config/models.py` (`Datasource`)

## Public Surface

| method | returns | meaning |
| --- | --- | --- |
| `list()` | `list[DatasourceInfo]` | Every configured datasource, in config order, failed ones included. |
| `get(datasource_id)` | `DatasourceInfo` | One configured datasource. Raises `ValueError` for an unknown id. |
| `add(datasource)` | `None` | Builds the adapter without connecting, registers it, and adds the `Datasource` to `engine.config`; its description and sample questions are what indexing reads. Raises `ValueError` if the id is taken, the type is unknown, a reference does not resolve or the adapter rejects its connection settings (with every secret masked). Index it next: `engine.index.build([datasource.id])`. |
| `remove(datasource_id)` | `None` | Takes the datasource out of routing and out of `engine.config`, and closes its adapter's pool. Its index entries stay until the next full `index.build` or `index.clear`. Raises `ValueError` for an unknown id. |
| `schema(datasource_id)` | `dict` | The indexed schema as the planner is given it: `{"datasource_id", "tables": [...]}`, tables sorted by name, each with `name`, `schema`, `row_count`, `description`, `columns` (`name`, `type`, `nullable`, `primary_key`, `description`) and `foreign_keys` (`columns`, `references_table`, `references_columns`). Read from the snapshot the datasource's live index build was made from, not the database; before the first index `tables` is empty. |
| `test(datasource_id)` | `DatasourceStatus` | Connects to one datasource, as `check()` does: `ready` puts it back in routing, `failed` (stage `connect`, the reason and a fix) keeps it out. Raises `ValueError` for an unknown id. |

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

```python
from nl2sql import NL2SQL, Datasource

engine = NL2SQL.from_config("nl2sql.yaml")
engine.check()
[(d.id, d.status) for d in engine.datasources.list()]   # [('shop', 'ready'), ('hr', 'failed')]
engine.datasources.get("hr").reason

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
