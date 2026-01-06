# CLI Reference

The Command Line Interface (CLI) is the primary way to interact with the platform.

**Command**: `nl2sql`

## Common Arguments

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--query` | The natural language query to run. | Interactive mode |
| `--config` | Path to datasources YAML. | `configs/datasources.yaml` |
| `--id` | Target specific datasource (bypass routing). | auto-route |
| `--index` | Index the datasources in the vector store. | |
| `--vector-store` | Path to vector DB persistence dir. | `.chroma_db` |

## Advanced Arguments

| Argument | Description | Default |
| :--- | :--- | :--- |
| `--llm-config` | Path to LLM configuration. | `configs/llm_config.yaml` |
| `--user` | Context user ID for RBAC checks. | `admin` |
| `--json` | Output result as raw JSON (no logs). | |
| `--no-exec` | Generate and Validate SQL only (no execution). | |
| `--list-adapters` | List installed plugins. | |

## Benchmarking

| Argument | Description |
| :--- | :--- |
| `--benchmark` | Activate benchmark mode. |
| `--dataset` | Path to Golden Set YAML. |
| `--export-path` | Save results to file. |
