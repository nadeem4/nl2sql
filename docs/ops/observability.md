# Observability and Monitoring

The platform includes a comprehensive observability stack designed for production readiness, leveraging **OpenTelemetry**, **Structured Logging**, and **Forensic Audit Logs**.

## 1. Metrics & Tracing (OpenTelemetry)

We use **OpenTelemetry (OTel)** for vendor-neutral instrumentation.

### Configuration

Set the following environment variables:

- `OBSERVABILITY_EXPORTER="otlp"`: Enables the OTLP exporter (requires a collector like Jaeger or Datadog Agent).
- `OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4317"`: The endpoint for the collector (gRPC).

### Key Metrics

| Metric Name | Type | Unit | Attributes | Description |
| :--- | :--- | :--- | :--- | :--- |
| `nl2sql.token.usage` | Counter | `1` | `model`, `agent`, `datasource_id` | Total LLM tokens consumed. |
| `nl2sql.node.duration` | Histogram | `s` | `node`, `datasource_id` | Execution duration of graph nodes. |

### Visualization

- **Local**: Use [Jaeger](https://www.jaegertracing.io/) for traces and [Prometheus](https://prometheus.io/) for metrics.
- **Production**: Compatible with Datadog, Honeycomb, New Relic, etc.

## 2. Structured Logging

For production, logs are output in **JSON format** to facilitate parsing by aggregators (Splunk, ELK).

- **Activation**: JSON logging is automatically enabled when `OBSERVABILITY_EXPORTER="otlp"`.
- **Correlation**: Every log entry includes a `trace_id` and `tenant_id` (if authenticated) to correlate logs across the request lifecycle.

**Example Log Entry:**

```json
{
  "timestamp": "2024-01-01T12:00:00",
  "level": "INFO",
  "message": "Planning phase completed",
  "trace_id": "8a3c...",
  "tenant_id": "org_123",
  "node": "planner"
}
```

## 3. Persistent Audit Log

For forensic analysis and "Time Travel" debugging, the system maintains a separate, persistent audit log.

- **Location**: `logs/audit_events.log` (Rotation enabled: 10MB x 5 backups).
- **Content**: detailed record of AI Decisions (Prompt inputs, Model responses, Token usage).
- **Purpose**: Allows operators to answer "Why did the AI say X?" hours or days later.

**Event Structure:**

```json
{
  "timestamp": "...",
  "event_type": "llm_interaction",
  "trace_id": "...",
  "tenant_id": "...",
  "data": {
    "agent": "planner",
    "model": "gpt-4o",
    "response_snippet": "SELECT * FROM...",
    "token_usage": {"total_tokens": 150}
  }
}
```

## 4. Legacy Tooling

The CLI `Performance Tree` is preserved for local development convenience but piggybacks on the same instrumentation hooks.
