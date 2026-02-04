# Getting Started

NL2SQL supports three ways to get started. Choose the guide that matches how you want
to use the platform:

- **PyPI (Python API)**: Install `nl2sql-core` and use it programmatically.
- **Docker (REST API)**: Run the API service and integrate over HTTP.
- **From Source (Development)**: Clone the repo for local development and contributions.

## Choose your path

- [PyPI (Python API)](pypi.md)
- [Docker (REST API)](docker.md)
- [From Source (Development)](source.md)

## Configuration prerequisites

All paths require configuration files. Start with the example configs:

- `configs/datasources.example.yaml` → copy to `configs/datasources.yaml`
- `configs/llm.example.yaml` → copy to `configs/llm.yaml`
- `configs/policies.example.json` → copy to `configs/policies.json`
- `configs/secrets.example.yaml` → copy to `configs/secrets.yaml` (optional)

See `configuration/system.md` for environment variables and defaults.
