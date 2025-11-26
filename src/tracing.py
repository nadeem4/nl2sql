from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Optional

from opentelemetry import trace
from opentelemetry.trace import Tracer


def get_tracer() -> Tracer:
    return trace.get_tracer("nl2sql")


@contextmanager
def span(name: str, attributes: Optional[Dict[str, Any]] = None):
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as s:
        if attributes:
            for k, v in attributes.items():
                try:
                    s.set_attribute(k, v)
                except Exception:
                    continue
        yield s
