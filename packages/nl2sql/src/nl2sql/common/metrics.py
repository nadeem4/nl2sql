"""Performance metrics tracking with OpenTelemetry support."""
from typing import List, Dict, Any, Optional
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

# Per-node latency events from PipelineMonitorCallback. Token usage is per run,
# in QueryResult.usage (TokenUsageCallback), not in a process-wide list.
LATENCY_LOG: List[Dict[str, Any]] = []

_meter = metrics.get_meter("nl2sql.core")
node_duration_histogram = _meter.create_histogram(
    name="nl2sql.node.duration",
    description="Duration of node execution in seconds",
    unit="s",
)
token_usage_counter = _meter.create_counter(
    name="nl2sql.token.usage",
    description="LLM tokens, by node, model, datasource_id and type "
    "(input, cached_input, cache_write_input, output, reasoning, total)",
    unit="1",
)


def configure_metrics(exporter_type: str = "none", otlp_endpoint: Optional[str] = None):
    """Configures the OpenTelemetry Metric Provider.
    
    Args:
        exporter_type: 'none', 'console', or 'otlp'
        otlp_endpoint: Optional endpoint for OTLP exporter
    """
    if exporter_type == "none":
        return

    reader = None
    if exporter_type == "console":
        reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
    elif exporter_type == "otlp":
        endpoint = otlp_endpoint or "http://localhost:4317"
        reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
    
    if reader:
        provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(provider)


def reset_usage():
    """Resets the latency log."""
    LATENCY_LOG.clear()
