# NL2SQL API Documentation

The NL2SQL API provides a REST interface to the NL2SQL engine, allowing external clients (such as the TypeScript CLI) to interact with the system.

## Two-Tier API Architecture

NL2SQL provides a two-tier API architecture:

### 1. Core API (Python)
- **Location**: Within the core package (`nl2sql` module)
- **Interface**: Direct Python class interface (`NL2SQL` class)
- **Use Case**: Direct Python integration, embedded applications
- **Access**: Import and use directly in Python code

### 2. REST API (HTTP) - This Package
- **Location**: API package (`nl2sql-api`)
- **Interface**: HTTP REST endpoints
- **Use Case**: Remote clients, web applications, TypeScript CLI
- **Access**: HTTP requests to API endpoints

This REST API package serves as a bridge between external HTTP clients and the core NL2SQL engine, using the core's public API internally.

## Available Endpoints

### Query Endpoint
- **URL**: `/api/v1/query`
- **Method**: `POST`
- **Description**: Execute a natural language query against a database
- **Request Body**:
  ```json
  {
    "natural_language": "Show top 10 customers by revenue",
    "datasource_id": "optional_datasource_id",
    "execute": true,
    "user_context": {
      "user_id": "user123",
      "permissions": ["read_customers", "read_orders"]
    }
  }
  ```
- **Response**:
  ```json
  {
    "sql": "SELECT customer_name, revenue FROM customers ORDER BY revenue DESC LIMIT 10",
    "results": [...],
    "final_answer": "Here are the top 10 customers by revenue...",
    "errors": [],
    "trace_id": "unique_trace_id",
    "reasoning": [...],
    "warnings": [...]
  }
  ```

### Schema Endpoints
- **URL**: `/api/v1/schema/{datasource_id}`
- **Method**: `GET`
- **Description**: Get schema information for a specific datasource
- **Response**:
  ```json
  {
    "datasource_id": "my_database",
    "tables": [...],
    "relationships": [...],
    "metadata": {...}
  }
  ```

- **URL**: `/api/v1/schema`
- **Method**: `GET`
- **Description**: List all available datasources
- **Response**: `["datasource1", "datasource2", ...]`

### Datasource Management Endpoints
- **URL**: `/api/v1/datasource`
- **Method**: `POST`
- **Description**: Add a new datasource programmatically
- **Request Body**:
  ```json
  {
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
  }
  ```
- **Response**:
  ```json
  {
    "success": true,
    "message": "Datasource 'my_postgres_db' added successfully",
    "datasource_id": "my_postgres_db"
  }
  ```

- **URL**: `/api/v1/datasource`
- **Method**: `GET`
- **Description**: List all registered datasources
- **Response**:
  ```json
  {
    "datasources": ["db1", "db2", "..."]
  }
  ```

- **URL**: `/api/v1/datasource/{datasource_id}`
- **Method**: `GET`
- **Description**: Get details of a specific datasource
- **Response**:
  ```json
  {
    "datasource_id": "my_postgres_db",
    "exists": true
  }
  ```

- **URL**: `/api/v1/datasource/{datasource_id}`
- **Method**: `DELETE`
- **Description**: Remove a datasource (not currently supported)
- **Response**:
  ```json
  {
    "success": false,
    "message": "Removing datasources is not currently supported by the engine",
    "datasource_id": "my_postgres_db"
  }
  ```

### LLM Management Endpoints
- **URL**: `/api/v1/llm`
- **Method**: `POST`
- **Description**: Configure an LLM programmatically
- **Request Body**:
  ```json
  {
    "config": {
      "name": "my_openai_model",
      "provider": "openai",
      "model": "gpt-4o",
      "api_key": "${SECRET_OPENAI_API_KEY}",
      "temperature": 0.0
    }
  }
  ```
- **Response**:
  ```json
  {
    "success": true,
    "message": "LLM 'my_openai_model' configured successfully",
    "llm_name": "my_openai_model"
  }
  ```

- **URL**: `/api/v1/llm`
- **Method**: `GET`
- **Description**: List all configured LLMs
- **Response**:
  ```json
  {
    "llms": ["default"]
  }
  ```

- **URL**: `/api/v1/llm/{llm_name}`
- **Method**: `GET`
- **Description**: Get details of a specific LLM
- **Response**:
  ```json
  {
    "llm_name": "my_openai_model",
    "configured": true
  }
  ```

### Indexing Management Endpoints
- **URL**: `/api/v1/index/{datasource_id}`
- **Method**: `POST`
- **Description**: Index schema for a specific datasource
- **Response**:
  ```json
  {
    "success": true,
    "datasource_id": "my_postgres_db",
    "indexing_stats": {
      "chunks": 15,
      "documents": 15
    },
    "message": "Successfully indexed datasource 'my_postgres_db'"
  }
  ```

- **URL**: `/api/v1/index-all`
- **Method**: `POST`
- **Description**: Index schema for all registered datasources
- **Response**:
  ```json
  {
    "success": true,
    "indexing_results": {
      "my_postgres_db": {
        "chunks": 15,
        "documents": 15
      },
      "my_mysql_db": {
        "chunks": 12,
        "documents": 12
      }
    },
    "message": "Successfully initiated indexing for all datasources"
  }
  ```

- **URL**: `/api/v1/index`
- **Method**: `DELETE`
- **Description**: Clear the vector store index
- **Response**:
  ```json
  {
    "success": true,
    "message": "Index cleared successfully"
  }
  ```

- **URL**: `/api/v1/index/status`
- **Method**: `GET`
- **Description**: Get the status of the index
- **Response**:
  ```json
  {
    "status": "operational",
    "indexed_datasources": ["my_postgres_db", "my_mysql_db"],
    "total_indexes": 2
  }
  ```

### Health Check Endpoints
- **URL**: `/api/v1/health`
- **Method**: `GET`
- **Description**: Check if the API is running
- **Response**:
  ```json
  {
    "success": true,
    "message": "NL2SQL API is running"
  }
  ```

- **URL**: `/api/v1/ready`
- **Method**: `GET`
- **Description**: Check if the API is ready to serve requests
- **Response**:
  ```json
  {
    "success": true,
    "message": "NL2SQL API is ready"
  }
  ```

## Running the API Server

To start the API server:

```bash
nl2sql-api --host 0.0.0.0 --port 8000 --reload
```

Or using uvicorn directly:

```bash
uvicorn nl2sql_api.main:app --host 0.0.0.0 --port 8000 --reload
```

## Configuration

The API relies on the same configuration files as the core NL2SQL engine:
- `configs/datasources.yaml` - Database connection configurations
- `configs/llm.yaml` - LLM provider configurations
- `configs/secrets.yaml` - Secret management configurations

Make sure these files are properly configured before starting the API server.