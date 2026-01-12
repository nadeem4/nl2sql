# Observability

## Logging

We use a structured logging approach suitable for production environments (Splunk, Datadog, ELK).

* **Format**: JSON (Production) or Human-Readable (Dev).
* **Attributes**: Logs include `request_id`, `user_id`, `node_name`, and `execution_time`.

### Enabling JSON Logs

Set the environment variable or use the flag:

```bash
export LOG_FORMAT=json
# or
nl2sql run "query" --json-logs
```

::: nl2sql.common.logger.JsonFormatter

## Tracing

The platform is instrumented with [LangSmith](https://smith.langchain.com/) for deep tracing of the Agentic Graph.

1. Set `LANGCHAIN_TRACING_V2=true`.
2. Set `LANGCHAIN_API_KEY=...`.

This will stream full traces of the Planner, Validator, and Generator steps to the LangSmith dashboard.
