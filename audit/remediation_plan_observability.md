# Remediation Plan: Observability & Reliability

**Source Audit**: `production_readiness_report.md`
**Date**: 2026-01-13
**Focus**: Telemetry, Forensics, and Production Visibility.

---

## 🚀 High Priority Enhancements

### [ ] **ENH-OBS-001: OpenTelemetry Integration** (P0 - Critical)

- **Goal**: Enable standard APM features (Datadog, Jaeger, Honeycomb) for trace and metric visualization.
- **Problem**: Current metrics (`LATENCY_LOG`) are in-memory only and lost on restart. No visualization of latency distribution.
- **Implementation**:
  - [ ] **Wire Up**: Connect `monitor.py` to the existing `opentelemetry-sdk` (dependencies already present).
  - [ ] **Refactor**: Update `nl2sql.common.metrics` to replace in-memory lists with `MeterProvider`.
  - [ ] **Instrument**: Update `monitor.py` to record OTeL Histograms for node execution duration.
  - [ ] **Instrument**: Update `TokenHandler` to record OTeL Counters for token usage.
  - [ ] **Config**: Wire up `OBSERVABILITY_EXPORTER` setting to initialize the generic exporter in `monitor.py`.

### Backend Strategy (Local & Prod)
>
> **Why OTLP?** It decouples Python code from the backend. Code sends to `OTLP Collector`, which routes data.

- **Traces** (Waterfalls): Sent to **Jaeger** (Local) or Datadog/Honeycomb (Prod).
- **Metrics** (Latency/Errors): Sent to **Prometheus** (Local) or Datadog (Prod).
- **Visualization**: Use **Grafana** to view Prometheus metrics and Jaeger traces in one UI.

### [ ] **ENH-OBS-002: Structured Logging (JSON)** (P0 - Critical)

- **Goal**: Machine-readable logs for ingestion by Splunk/ELK/Datadog.
- **Problem**: Logs are text-based and lack easy parsing for fields like `trace_id` or `user_id`.
- **Implementation**:
  - [ ] **Enable**: Wire `OBSERVABILITY_EXPORTER=otlp` to trigger the existing `JsonFormatter` in `configure_logging()`.
  - [ ] **Verify**: Ensure `trace_id` injection (already implemented in `TraceContextFilter`) works correctly with the JSON output.

### [ ] **ENH-OBS-003: Persistent Audit Log** (P1 - High)

- **Goal**: Forensic "Time Travel" debugging for AI decisions.
- **Problem**: "Reasoning" is transient. We cannot explain past AI decisions to customers.
- **Implementation**:
  - [ ] Create `EventLogger` class.
  - [ ] Log `{trace_id, timestamp, node, prompt_text, response_text, model, tokens}` to a persistent store (initially `events.log` rotated file, extensible to DB).
  - [ ] Ensure PII/Secrets are sanitized before logging prompts.

## 🛠️ Medium Priority

### [ ] **ENH-OBS-004: Tenant Context Propagation** (P2 - Medium)

- **Goal**: Multi-tenant observability.
- **Problem**: Logs don't consistently show which tenant/user initiated the request.
- **Implementation**:
  - [ ] **Schema Validation**: Define strict Pydantic model for `user_context` in `GraphState` (currently untyped Dict).
  - [ ] **Correlation**: Inject `tenant_id` from `user_context` into `trace_context` for log correlation.

---

## 📉 Success Metrics

- **Latency Visibility**: Can view p95 latency per node in APM.
- **Error Tracking**: Can alert on "Validation Failure Rate > 5%".
- **Cost Tracking**: Can verify "Token Usage per Tenant".
- **Debuggability**: Can retrieve the exact prompt that caused a specific error 24 hours later.
