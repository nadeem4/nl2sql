# NL2SQL Public API

The NL2SQL Public API provides a clean, stable interface to the NL2SQL core engine functionality. This API defines the official boundaries for external consumers and ensures backward compatibility.

## Two-Tier API Architecture

NL2SQL provides a two-tier API architecture:

### 1. Core API (Python)
- **Location**: Within the core package (`nl2sql` module)
- **Interface**: Direct Python class interface (`NL2SQL` class)
- **Use Case**: Direct Python integration, embedded applications
- **Access**: Import and use directly in Python code

### 2. REST API (HTTP)
- **Location**: API package (`nl2sql-api`)
- **Interface**: HTTP REST endpoints
- **Use Case**: Remote clients, web applications, TypeScript CLI
- **Access**: HTTP requests to API endpoints

Both APIs provide access to the same underlying NL2SQL engine functionality, allowing flexible integration options.

## Main Class: NL2SQL

The main entry point for interacting with the NL2SQL engine.

### Initialization

```python
from nl2sql import NL2SQL

# Initialize with configuration files
engine = NL2SQL(
    ds_config_path="configs/datasources.yaml",
    secrets_config_path="configs/secrets.yaml", 
    llm_config_path="configs/llm.yaml",
    vector_store_path="./vector_store",
    policies_config_path="configs/policies.json"
)

# Or initialize without config (will use defaults/settings)
engine = NL2SQL()
```

### Modular API Structure

The NL2SQL engine provides modular APIs for different functionality areas:

- `engine.query` - Query execution API
- `engine.datasource` - Datasource management API
- `engine.llm` - LLM configuration API
- `engine.indexing` - Schema indexing API
- `engine.auth` - Authentication and RBAC API
- `engine.settings` - Configuration and settings API
- `engine.results` - Result management API

Each module has its own focused functionality while being accessible through the main engine instance.

## Query API

### `run_query(natural_language, datasource_id=None, execute=True, user_context=None)`

Execute a natural language query against the database.

```python
result = engine.run_query("Show top 10 customers by revenue")
print(result.final_answer)
```

**Parameters:**
- `natural_language` (str): The natural language query to execute
- `datasource_id` (str, optional): Specific datasource to query (otherwise auto-resolved)
- `execute` (bool): Whether to execute the SQL against the database (default: True)
- `user_context` (UserContext, optional): User context for permissions

**Returns:** `QueryResult` object with query results.

## Datasource API

### `add_datasource(config)`

Programmatically add a datasource to the engine.

```python
from nl2sql.datasources.models import DatasourceConfig

config = {
    "id": "my_postgres_db",
    "description": "My PostgreSQL database",
    "connection": {
        "type": "postgres",
        "host": "localhost",
        "port": 5432,
        "database": "mydb",
        "user": "${SECRET_POSTGRES_USER}",
        "password": "${SECRET_POSTGRES_PASSWORD}"
    }
}

engine.add_datasource(config)
```

**Parameters:**
- `config` (DatasourceConfig or dict): Datasource configuration

### `add_datasource_from_config(config_path)`

Add datasources from a configuration file.

```python
engine.add_datasource_from_config("my_datasources.yaml")
```

**Parameters:**
- `config_path` (str or Path): Path to the datasource configuration file

### `list_datasources()`

List all registered datasource IDs.

```python
datasources = engine.list_datasources()
print(datasources)  # ['db1', 'db2', ...]
```

**Returns:** List of datasource IDs.

## LLM API

### `configure_llm(config)`

Programmatically configure an LLM.

```python
llm_config = {
    "name": "my_openai_model",
    "provider": "openai", 
    "model": "gpt-4o",
    "api_key": "${SECRET_OPENAI_API_KEY}",
    "temperature": 0.0
}

engine.configure_llm(llm_config)
```

**Parameters:**
- `config` (AgentConfig or dict): LLM configuration

### `configure_llm_from_config(config_path)`

Configure LLMs from a configuration file.

```python
engine.configure_llm_from_config("my_llm_config.yaml")
```

**Parameters:**
- `config_path` (str or Path): Path to the LLM configuration file

## Indexing API

### `index_datasource(datasource_id)`

Index schema for a specific datasource.

```python
stats = engine.index_datasource("my_postgres_db")
print(stats)  # Indexing statistics
```

**Parameters:**
- `datasource_id` (str): ID of the datasource to index

**Returns:** Dictionary with indexing statistics.

### `index_all_datasources()`

Index schema for all registered datasources.

```python
all_stats = engine.index_all_datasources()
for ds_id, stats in all_stats.items():
    print(f"{ds_id}: {stats}")
```

**Returns:** Dictionary mapping datasource IDs to indexing statistics.

### `clear_index()`

Clear the vector store index.

```python
engine.clear_index()
```

## Auth API

### `check_permissions(user_context, datasource_id, table)`

Check if a user has permission to access a specific resource.

```python
from nl2sql.auth.models import UserContext

user_ctx = UserContext(
    user_id="user123",
    tenant_id="tenant1",
    roles=["admin", "viewer"]
)

can_access = engine.check_permissions(user_ctx, "my_db", "customers")
```

**Parameters:**
- `user_context`: User context with roles
- `datasource_id` (str): ID of the datasource
- `table` (str): Name of the table

**Returns:** Boolean indicating if user has permission.

### `get_allowed_resources(user_context)`

Get resources a user has access to.

```python
resources = engine.get_allowed_resources(user_ctx)
print(resources["datasources"])  # List of allowed datasources
print(resources["tables"])      # List of allowed tables
```

**Parameters:**
- `user_context`: User context with roles

**Returns:** Dictionary with allowed datasources and tables.

## Settings API

### `get_current_settings()`

Get the current application settings.

```python
settings = engine.get_current_settings()
print(settings["global_timeout_sec"])
```

**Returns:** Dictionary of current settings.

### `get_setting(key)`

Get a specific setting value.

```python
timeout = engine.get_setting("global_timeout_sec")
```

**Parameters:**
- `key` (str): Setting key to retrieve

**Returns:** Value of the setting.

### `validate_configuration()`

Validate the current configuration.

```python
is_valid = engine.validate_configuration()
```

**Returns:** Boolean indicating if configuration is valid.



## Result API

### `store_query_result(frame, metadata=None)`

Store a query result in the result store.

```python
from nl2sql_adapter_sdk.contracts import ResultFrame

frame = ResultFrame.from_row_dicts([{"id": 1, "name": "John"}])
result_id = engine.store_query_result(frame, {"query": "SELECT * FROM users"})
```

**Parameters:**
- `frame`: ResultFrame containing the query results
- `metadata` (dict, optional): Metadata to associate with the result

**Returns:** Unique result ID.

### `retrieve_query_result(result_id)`

Retrieve a stored query result.

```python
frame = engine.retrieve_query_result(result_id)
rows = frame.to_row_dicts()
```

**Parameters:**
- `result_id` (str): ID of the result to retrieve

**Returns:** ResultFrame containing the query results.

### `get_result_metadata(result_id)`

Get metadata associated with a stored result.

```python
metadata = engine.get_result_metadata(result_id)
```

**Parameters:**
- `result_id` (str): ID of the result

**Returns:** Dictionary containing result metadata.

## QueryResult Object

The `run_query` method returns a `QueryResult` object with the following attributes:

- `sql` (str, optional): Generated SQL query
- `results` (list): Query execution results
- `final_answer` (str, optional): Natural language answer
- `errors` (list): List of errors encountered
- `trace_id` (str, optional): Unique trace identifier
- `reasoning` (list): Reasoning steps taken
- `warnings` (list): Warning messages

## Example Usage

```python
from nl2sql import NL2SQL

# Initialize the engine
engine = NL2SQL(
    ds_config_path="configs/datasources.yaml",
    llm_config_path="configs/llm.yaml"
)

# Add a datasource programmatically
engine.add_datasource({
    "id": "sales_db",
    "description": "Sales database",
    "connection": {
        "type": "postgres",
        "host": "localhost",
        "port": 5432,
        "database": "sales",
        "user": "${DB_USER}",
        "password": "${DB_PASS}"
    }
})

# Create a user context with permissions
from nl2sql.auth.models import UserContext

user_ctx = UserContext(
    user_id="user123",
    roles=["admin"]
)

# Check permissions
if engine.check_permissions(user_ctx, "sales_db", "orders"):
    # Index the newly added datasource
    engine.index_datasource("sales_db")

    # Run a query
    result = engine.run_query("What were total sales last month?", user_context=user_ctx)
    print(result.final_answer)

# List all datasources
print(engine.list_datasources())

# Use modular APIs directly
print(engine.datasource.list_datasources())
stats = engine.indexing.index_all_datasources()

# Check current settings
timeout = engine.get_setting("global_timeout_sec")
print(f"Timeout setting: {timeout}s")
```