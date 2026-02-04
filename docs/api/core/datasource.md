# Datasource API

## Purpose
Register, validate, and inspect datasources backed by adapter implementations.

## Responsibilities
- Register datasources from config or programmatically.
- Validate datasource connections via adapters.
- Expose adapter capabilities and details.

## Key Modules
- `packages/core/src/nl2sql/api/datasource_api.py`
- `packages/core/src/nl2sql/datasources/registry.py`
- `packages/core/src/nl2sql/datasources/models.py`
- `packages/core/src/nl2sql/datasources/discovery.py`

## Public Surface

### ConnectionConfig

Source:
`packages/core/src/nl2sql/datasources/models.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `type` | `str` | yes | Adapter type (e.g., `postgres`, `mysql`). |
| `...` | `Any` | no | Adapter-specific connection fields (allowed via `extra`). |

### DatasourceConfig

Source:
`packages/core/src/nl2sql/datasources/models.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `id` | `str` | yes | Datasource identifier. |
| `description` | `Optional[str]` | no | Human-readable description. |
| `connection` | `ConnectionConfig` | yes | Connection details. |
| `options` | `Dict[str, Any]` | no | Limits and adapter options. |

### DatasourceFileConfig

Source:
`packages/core/src/nl2sql/configs/datasources.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `version` | `int` | yes | Schema version (defaults to 1). |
| `datasources` | `List[DatasourceConfig]` | yes | Datasource definitions. |

### DatasourceAPI.add_datasource

Source:
`packages/core/src/nl2sql/api/datasource_api.py`

Signature:
`add_datasource(config: Union[DatasourceConfig, Dict[str, Any]]) -> None`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `config` | `DatasourceConfig | Dict[str, Any]` | yes | Datasource configuration. |

Returns:
`None`.

Raises:
- `ValueError` if datasource ID is missing or adapter type is unknown.
- `ValueError` if secret resolution fails.

Side Effects:
- Registers adapter instance in `DatasourceRegistry`.

Idempotency:
- Not strictly idempotent; re-registering the same ID overwrites in-memory registry.

### DatasourceAPI.add_datasource_from_config

Source:
`packages/core/src/nl2sql/api/datasource_api.py`

Signature:
`add_datasource_from_config(config_path: Union[str, pathlib.Path]) -> None`

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `config_path` | `Union[str, pathlib.Path]` | yes | Path to `datasources.yaml`. |

Returns:
`None`.

Raises:
- `FileNotFoundError` if config is missing.
- `RuntimeError` if PyYAML is not installed.
- `ValueError` for schema validation or adapter initialization errors.

Side Effects:
- Registers all datasources in registry.

Idempotency:
No (registrations happen each call).

### DatasourceAPI.list_datasources

Signature:
`list_datasources() -> List[str]`

Returns:
List of registered datasource IDs.

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
Adapter-derived metadata (connection args, limits, capabilities).

Raises:
`ValueError` if datasource ID is unknown.

## Behavioral Contracts
- Adapter discovery is via entry points group `nl2sql.adapters`.
- Connection secrets are resolved before adapter instantiation.
- Capabilities default to `SUPPORTS_SQL` if adapter does not declare capabilities.
