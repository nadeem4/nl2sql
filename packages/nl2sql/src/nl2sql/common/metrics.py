"""Performance metrics tracking with OpenTelemetry support."""
from typing import List, Dict, Any, Optional
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

# Legacy lists for CLI compatibility
TOKEN_LOG: List[Dict[str, Any]] = []
LATENCY_LOG: List[Dict[str, Any]] = []

_meter = metrics.get_meter("nl2sql.core")
node_duration_histogram = _meter.create_histogram(
    name="nl2sql.node.duration",
    description="Duration of node execution in seconds",
    unit="s",
)
token_usage_counter = _meter.create_counter(
    name="nl2sql.token.usage",
    description="Number of tokens used by LLM interactions",
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
    """Resets the token and latency logs."""
    TOKEN_LOG.clear()
    LATENCY_LOG.clear()
