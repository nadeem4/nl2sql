# Adapter SDK

The `nl2sql-adapter-sdk` defines the contract for new plugins.

## Models

### `DatasourceAdapter` (Abstract Base Class)

The main entry point. Implementations must provide:

- `fetch_schema()`
- `execute(sql)`
- `cost_estimate(sql)`

### `Table`

Schema definition.

```python
name: str
columns: List[Column]
foreign_keys: List[ForeignKey]
```

Flags that control the `GeneratorNode`.

| Flag | Description |
| :--- | :--- |
| `supports_limit_offset` | Generator will use LIMIT/OFFSET syntax. |
| `supports_dry_run` | PhysicalValidator will attempt `dry_run()`. |
| `supports_cost_estimation` | PhysicalValidator will check row counts. |
