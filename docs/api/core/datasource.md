# Datasource API

## Purpose
Register, validate, and inspect datasources backed by adapter implementations.

## Responsibilities
- Register datasources from `nl2sql.yaml` or programmatically.
- Validate datasource connections via adapters.
- Expose adapter capabilities and details.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/datasource_api.py`
- `packages/nl2sql/src/nl2sql/datasources/registry.py`
- `packages/nl2sql/src/nl2sql/config/models.py` (`Datasource`)
- `packages/nl2sql/src/nl2sql/registry.py` (adapter lookup by `type`)

## Public Surface

### DatasourceAPI.add_datasource

Source:
`packages/nl2sql/src/nl2sql/api/datasource_api.py`

Signature:
`add_datasource(config: Datasource) -> None`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `config` | `Datasource` | yes | A `Datasource` (see [Datasources](../../configuration/datasources.md)). |

Returns:
`None`.

Raises:
- `ValueError` if the id is already configured, the adapter type is unknown, or a reference does not resolve.

Side Effects:
- Builds the adapter without connecting, registers it, and adds the datasource
  to `engine.config`. Its description and sample questions are what indexing
  reads.

Idempotency:
- Not idempotent: adding an id that is already configured raises.

### DatasourceAPI.add_datasource_from_config

Source:
`packages/nl2sql/src/nl2sql/api/datasource_api.py`

Signature:
`add_datasource_from_config(config_path: Union[str, pathlib.Path]) -> None`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `config_path` | `Union[str, pathlib.Path]` | yes | Path to another `nl2sql.yaml`; its datasources are added. |

Returns:
`None`.

Raises:
- `ConfigError` if the file does not load, naming every problem.
- `ValueError` if an id is already configured or an adapter cannot be built.

Side Effects:
- Adds each datasource as `add_datasource` does: built, not connected.

Idempotency:
No (registrations happen each call).

### DatasourceAPI.list_datasources

Signature:
`list_datasources() -> List[str]`

Returns:
List of available datasource IDs. A datasource `check()` could not connect to
is not in it; `NL2SQL.list_unavailable_datasources()` maps each one to the
reason.

### DatasourceAPI.get_adapter

Signature:
`get_adapter(datasource_id: str) -> DatasourceAdapterProtocol`

Raises:
`ValueError` if datasource ID is unknown.

Side Effects:
None.

### DatasourceAPI.get_capabilities

Signature:
`get_capabilities(datasource_id: str) -> List[str]`

Raises:
`ValueError` if datasource ID is unknown.

### DatasourceAPI.validate_connection

Signature:
`validate_connection(ds_id: str) -> bool`

Returns:
`True` if adapter connection check succeeds.

Raises:
Adapter-specific exceptions from `test_connection()`.

Side Effects:
Per-adapter network/database connection attempts.

### DatasourceAPI.get_datasource_details

Signature:
`get_datasource_details(datasource_id: str) -> Dict[str, Any]`

Returns:
Adapter-derived metadata (connection args, limits, capabilities). Connection arg
values whose key contains `password`, `secret`, `token`, or `api_key`
(case-insensitive) are masked as `"***"` before being returned.

Raises:
`ValueError` if datasource ID is unknown.

## Behavioral Contracts
- Adapter discovery is via entry points group `nl2sql.adapters`.
- Connection secrets are resolved before adapter instantiation.
- Capabilities default to `SUPPORTS_SQL` if adapter does not declare capabilities.
