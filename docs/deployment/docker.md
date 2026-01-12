# Docker Deployment

The simplest way to run the NL2SQL Platform is via Docker.

## Prerequisites

* Docker Engine 20.10+
* Access to your target databases

## Quick Start

```bash
docker run -d \
  --name nl2sql \
  -p 8000:8000 \
  -e OPENAI_API_KEY=sk-...\
  -e POSTGRES_URL=postgresql://user:pass@host:5432/db \
  ghcr.io/nl2sql/platform:latest
```

## Environment Variables

| Variable | Description | Required | Default |
| :--- | :--- | :--- | :--- |
| `OPENAI_API_KEY` | Key for the LLM provider. | Yes | - |
| `LOG_LEVEL` | Logging verbosity (DEBUG, INFO, WARN). | No | INFO |
| `WORKERS` | Number of Gunicorn workers. | No | 4 |

## Volume Mounting (Optional)

If you are using a local Vector Store for schema indexing, mount the storage directory to persist the index across restarts.

```bash
docker run -v ./data:/app/data ...
```
