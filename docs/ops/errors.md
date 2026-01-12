# Error Reference

This guide lists the standard error codes returned by the NL2SQL Platform.

## Standard Error Codes

| Error Code | Severity | Description | Troubleshooting |
| :--- | :--- | :--- | :--- |
| `SECURITY_VIOLATION` | **CRITICAL** | The query attempted an illegal operation (DROP/DELETE) or accessed a restricted table. | Check `policies.json` for proper access rights. |
| `CONNECTION_FAILED` | HIGH | Could not reach the target datasource. | Verify network connectivity and invalid credentials in `datasources.yaml`. |
| `PLANNING_FAILURE` | MEDIUM | The LLM could not generate a valid plan for the query. | Query may be too ambiguous; try rephrasing or adding synonyms to semantic search. |
| `EXECUTION_TIMEOUT` | MEDIUM | The query took longer than the configured limit. | Optimize the database index or increase `timeout_ms` in config. |
| `INVALID_SYNTAX` | LOW | The generated SQL was invalid for the dialect. | Please report this as a bug with the specific query. |
