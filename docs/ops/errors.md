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
| `INTENT_VIOLATION` | **CRITICAL** | Adversarial prompt detected (Jailbreak, PII). | **Non-Retryable**. Check security logs. |
| `SAFEGUARD_VIOLATION` | **CRITICAL** | Response contained restricted data. | **Non-Retryable**. Check DLP rules. |
| `MISSING_DATASOURCE_ID` | **CRITICAL** | No datasource selected in state. | **Non-Retryable**. Check router configuration. |
| `MISSING_LLM` | **CRITICAL** | Agent node has no LLM configured. | **Non-Retryable**. Check `llm.yaml`. |

## Reliability & Retry Logic

The platform implements **Selective Retries** with **Exponential Backoff** to handle transient failures without causing system overload.

### Automatic Retries

The following errors trigger an automatic retry loop (up to 3 attempts):

- `PLANNING_FAILURE` (LLM output issues)
- `COLUMN_NOT_FOUND` (Schema drift/hallucination)
- `DB_EXECUTION_ERROR` (Transient timeouts)
- `VALIDATOR_CRASH` (Service glitches)

### Backoff Strategy

The system uses `min(10s, 2^retries)` + Random Jitter (0-500ms).

- Attempt 1: ~1.5s delay
- Attempt 2: ~2.5s delay
- Attempt 3: ~4.5s delay

### Fail Fast

**Critical Security and Configuration errors** (e.g., `SECURITY_VIOLATION`, `INTENT_VIOLATION`) cause the pipeline to halt immediately. This prevents brute-force attacks and infinite loops on fatal misconfigurations.
