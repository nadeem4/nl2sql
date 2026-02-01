# NL2SQL API

API layer for the NL2SQL engine that provides a REST interface to the core functionality.

## Overview

This package provides a FastAPI-based REST API that uses the NL2SQL core's public API to interact with the NL2SQL engine over HTTP. It serves as a bridge between external clients (such as the TypeScript CLI) and the core engine functionality.

## Architecture

The API package leverages the NL2SQL core's public API layer (`NL2SQL` class), ensuring clean separation between the API service and the core engine implementation. The service layer uses the core's public methods like `run_query()`, `list_datasources()`, and schema access through `engine.context.schema_store`.

## Features

- RESTful API endpoints for natural language to SQL conversion
- Schema introspection capabilities
- Health and readiness checks
- Proper error handling and response formatting
- Lazy initialization to avoid configuration issues during import
- Integration with the core's public API layer

## Endpoints

- `POST /api/v1/query` - Execute a natural language query
- `GET /api/v1/schema/{datasource_id}` - Get schema for a specific datasource
- `GET /api/v1/schema` - List available datasources
- `GET /api/v1/health` - Health check endpoint
- `GET /api/v1/ready` - Readiness check endpoint

## Running the API

```bash
pip install -e .
nl2sql-api --host 0.0.0.0 --port 8000 --reload
```

Or using uvicorn directly:

```bash
uvicorn nl2sql_api.main:app --host 0.0.0.0 --port 8000 --reload
```

## Development

Install in development mode:

```bash
pip install -e .
```

For detailed API documentation, see [API_DOCS.md](API_DOCS.md).