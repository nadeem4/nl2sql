# Getting Started

This guide walks through the minimal steps to run the NL2SQL pipeline. NL2SQL provides a **two-tier API architecture** for flexible integration:

1. **Core API (Python)**: Direct Python integration using the `NL2SQL` class
2. **REST API (HTTP)**: HTTP endpoints for remote access and web applications

Choose the option that best fits your use case.

## Prerequisites

- Python 3.10+ (for Python integration)
- Docker (for API container deployment, optional)
- A configured datasource in `configs/datasources.yaml`
- An LLM configuration in `configs/llm.yaml`

## Installation Options

### Option 1: Install Core Package from PyPI

```bash
pip install nl2sql-core
```

### Option 2: Install from Local Source (Development)

```bash
pip install -e packages/core
pip install -e packages/adapter-sdk
```

If you are using the SQLAlchemy adapter, install it as well:

```bash
pip install -e packages/adapter-sqlalchemy
```

### Option 3: Install API Package

```bash
pip install -e packages/api
```

## Configuration

`NL2SQLContext` reads its configuration from these settings (see `nl2sql.common.settings.Settings`):

- `configs/datasources.yaml` (datasource definitions)
- `configs/llm.yaml` (agent model configuration)
- `configs/policies.json` (RBAC policies)
- `configs/secrets.yaml` (secret providers, optional)

Template examples exist in `configs/*.example.yaml` and `configs/*.example.json`.

## Two Integration Approaches

### Approach 1: Core API (Python) - Direct Integration

Use the Core API for direct Python integration in your applications.

#### Initialize the Engine

```python
from nl2sql import NL2SQL

# Initialize with configuration files
engine = NL2SQL(
    ds_config_path="configs/datasources.yaml",
    llm_config_path="configs/llm.yaml",
    secrets_config_path="configs/secrets.yaml",
    policies_config_path="configs/policies.json"
)

# Or initialize with default settings
engine = NL2SQL()
```

#### Index Schema (Required for Retrieval)

Before running queries, index your datasource schema:

```python
# Index a specific datasource
engine.index_datasource("your_datasource_id")

# Index all registered datasources
engine.index_all_datasources()

# Or use the lower-level approach
from nl2sql.context import NL2SQLContext
from nl2sql.indexing.orchestrator import IndexingOrchestrator

ctx = NL2SQLContext()
orchestrator = IndexingOrchestrator(ctx)

for adapter in ctx.ds_registry.list_adapters():
    orchestrator.index_datasource(adapter)
```

#### Run Queries

```python
from nl2sql.auth.models import UserContext

# Create a user context (optional)
user_ctx = UserContext(
    user_id="user123",
    roles=["admin"]
)

# Run a natural language query
result = engine.run_query(
    "Top 5 customers by revenue last quarter?",
    user_context=user_ctx
)

print(result.final_answer)
print(result.sql)
print(result.errors)
```

#### Manage Datasources Programmatically

```python
# Add a datasource programmatically
engine.add_datasource({
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
})

# List all datasources
datasources = engine.list_datasources()
print(datasources)
```

#### Configure LLMs Programmatically

```python
# Configure an LLM programmatically
engine.configure_llm({
    "name": "my_openai_model",
    "provider": "openai",
    "model": "gpt-4o",
    "api_key": "${SECRET_OPENAI_API_KEY}",
    "temperature": 0.0
})
```

### Approach 2: REST API (HTTP) - Remote Access

Use the REST API for remote access, web applications, or when you need HTTP-based integration.

#### Start the API Server

```bash
# Using the CLI command
nl2sql-api --host 0.0.0.0 --port 8000 --reload

# Or using uvicorn directly
uvicorn nl2sql_api.main:app --host 0.0.0.0 --port 8000 --reload
```

#### Use the REST API

Once the server is running, you can make HTTP requests:

```bash
# Execute a query
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{
    "natural_language": "Top 5 customers by revenue last quarter?",
    "datasource_id": "your_datasource_id",
    "execute": true
  }'

# Get schema for a specific datasource
curl http://localhost:8000/api/v1/schema/your_datasource_id

# List all datasources
curl http://localhost:8000/api/v1/schema

# Add a new datasource
curl -X POST http://localhost:8000/api/v1/datasource \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
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
  }'

# List all registered datasources
curl http://localhost:8000/api/v1/datasource

# Configure an LLM
curl -X POST http://localhost:8000/api/v1/llm \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "name": "my_openai_model",
      "provider": "openai",
      "model": "gpt-4o",
      "api_key": "${SECRET_OPENAI_API_KEY}",
      "temperature": 0.0
    }
  }'

# Index a specific datasource
curl -X POST http://localhost:8000/api/v1/index/my_postgres_db

# Index all datasources
curl -X POST http://localhost:8000/api/v1/index-all

# Get index status
curl http://localhost:8000/api/v1/index/status

# Health check
curl http://localhost:8000/api/v1/health
```

#### Python Client for REST API

You can also use Python to interact with the REST API:

```python
import requests

# Execute a query via REST API
response = requests.post("http://localhost:8000/api/v1/query", json={
    "natural_language": "Top 5 customers by revenue last quarter?",
    "datasource_id": "your_datasource_id",
    "execute": True
})

result = response.json()
print(result.get("final_answer"))
```

## API Comparison

| Feature | Core API (Python) | REST API (HTTP) |
|---------|------------------|-----------------|
| **Latency** | Lower (direct call) | Higher (network round-trip) |
| **Deployment** | Embedded in your app | Separate service |
| **Scaling** | Scale with your app | Independent scaling |
| **Security** | App-level security | Network-level security |
| **Use Cases** | Embedded, internal tools | Web apps, remote clients |
| **Configuration** | Python code | HTTP requests |

## Execution Flag

Both APIs accept an `execute` flag that determines whether to execute the generated SQL against the database:

- `execute=True` (default): Execute the SQL and return results
- `execute=False`: Return the generated SQL without execution

## Next Steps

- See `configuration/system.md` for configuration details and environment variable mapping.
- See `deployment/architecture.md` for production deployment guidance.
- See `api/index.md` for detailed API documentation.
- See `packages/core/PUBLIC_API_DOCS.md` for Core API reference.
- See `packages/api/API_DOCS.md` for REST API reference.
