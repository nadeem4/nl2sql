# NL2SQL API

API layer for the NL2SQL engine that provides a REST interface to the core functionality.

## Two-Tier API Architecture

NL2SQL provides a two-tier API architecture:

### 1. Core API (Python)
- **Location**: Core package (`nl2sql-core`)
- **Interface**: Direct Python class interface (`NL2SQL` class)
- **Use Case**: Direct Python integration, embedded applications
- **Access**: Import and use directly in Python code

### 2. REST API (HTTP) - This Package
- **Location**: API package (`nl2sql-api`) - This package
- **Interface**: HTTP REST endpoints
- **Use Case**: Remote clients, web applications, TypeScript CLI
- **Access**: HTTP requests to API endpoints

This REST API package serves as a bridge between external HTTP clients and the core NL2SQL engine, using the core's public API internally.

## Overview

This package provides a FastAPI-based REST API that uses the NL2SQL core's public API to interact with the NL2SQL engine over HTTP. It serves as a bridge between external clients (such as the TypeScript CLI) and the core engine functionality.

## Architecture

The API package leverages the NL2SQL core's public API layer (`NL2SQL` class), ensuring clean separation between the API service and the core engine implementation. The service layer uses the core's public methods like `run_query()`, `list_datasources()`, and schema access through `engine.context.schema_store`.

## Features

- RESTful API endpoints for natural language to SQL conversion
- Datasource and LLM management endpoints
- Health and readiness checks
- Proper error handling and response formatting
- Lazy initialization to avoid configuration issues during import
- Integration with the core's public API layer

## Endpoints

### Query Endpoints
- `POST /api/v1/query` - Execute a natural language query
- `GET /api/v1/health` - Health check endpoint
- `GET /api/v1/ready` - Readiness check endpoint

### Datasource Management Endpoints
- `POST /api/v1/datasource` - Add a new datasource programmatically
- `GET /api/v1/datasource` - List all registered datasources
- `GET /api/v1/datasource/{datasource_id}` - Get details of a specific datasource
- `DELETE /api/v1/datasource/{datasource_id}` - Remove a datasource (not currently supported)

### LLM Management Endpoints
- `POST /api/v1/llm` - Configure an LLM programmatically
- `GET /api/v1/llm` - List all configured LLMs
- `GET /api/v1/llm/{llm_name}` - Get details of a specific LLM

### Indexing Management Endpoints
- `POST /api/v1/index/{datasource_id}` - Index schema for a specific datasource
- `POST /api/v1/index-all` - Index schema for all registered datasources
- `DELETE /api/v1/index` - Clear the vector store index
- `GET /api/v1/index/status` - Get the status of the index

## Running the API

```bash
pip install -e .
nl2sql-api --host 0.0.0.0 --port 8000 --reload
```

Or using the server script directly:

```bash
python -m nl2sql_api.server --host 0.0.0.0 --port 8000 --reload
```

## Development

Install in development mode:

```bash
pip install -e .
```

For detailed API documentation, see [API_DOCS.md](API_DOCS.md).