"""OpenTelemetry tracing utilities."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Optional

from opentelemetry import trace
from opentelemetry.trace import Tracer


def get_tracer() -> Tracer:
    """Returns the application tracer."""
    return trace.get_tracer("nl2sql")


@contextmanager
def span(name: str, attributes: Optional[Dict[str, Any]] = None):
    """Context manager for creating a new trace span.

    Args:
        name (str): The name of the span.
        attributes (Optional[Dict[str, Any]]): Attributes to attach to the span.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as s:
        if attributes:
            for k, v in attributes.items():
                try:
                    s.set_attribute(k, v)
                except Exception:
                    continue
        yield s
