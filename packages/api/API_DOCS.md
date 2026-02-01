# NL2SQL API Documentation

The NL2SQL API provides a REST interface to the NL2SQL engine, allowing external clients (such as the TypeScript CLI) to interact with the system.

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