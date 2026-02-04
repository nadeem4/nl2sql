# Health API

## Endpoints

### `GET /api/v1/health`

Source: `packages/api/src/nl2sql_api/routes/health.py`

Response model: `SuccessResponse`

Returns:
`{"success": true, "message": "NL2SQL API is running"}`

### `GET /api/v1/ready`

Source: `packages/api/src/nl2sql_api/routes/health.py`

Response model: `SuccessResponse`

Returns:
`{"success": true, "message": "NL2SQL API is ready"}`
