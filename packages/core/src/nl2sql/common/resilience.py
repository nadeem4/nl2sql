"""
Resilience Module: Circuit Breakers and Fault Tolerance.

This module centralizes the configuration of Circuit Breakers using `pybreaker`.
It implements "Tiered Resilience" allowing the system to Fail Fast when 
downstream dependencies (LLM, Vector DB, SQL DB) are unavailable.

Features:
- Global Breaker Instances (LLM, DB, VECTOR)
- Observability via CircuitBreakerListener
- Safe Exclusion of "Soft Failures" (e.g., Rate Limits)
"""
import pybreaker
from typing import Any, Optional, List, Type
from nl2sql.common.logger import get_logger

logger = get_logger("resilience")

class ObservabilityListener(pybreaker.CircuitBreakerListener):
    """Listener to export circuit breaker state changes and failures to logs/metrics."""
    
    def state_change(self, cb, old_state, new_state):
        logger.warning(
            f"Circuit Breaker '{cb.name}' changed state: {old_state.name} -> {new_state.name}"
        )
        # TODO: Emit metric: breaker_state_change{name=cb.name, state=new_state.name}

    def failure(self, cb, exc):
        logger.error(
            f"Circuit Breaker '{cb.name}' recorded failure: {type(exc).__name__}: {exc}"
        )
        # TODO: Emit metric: breaker_failure{name=cb.name, error=type(exc).__name__}

    def success(self, cb):
        pass
        # TODO: Emit metric: breaker_success{name=cb.name}


def create_breaker(
    name: str, 
    fail_max: int = 5, 
    reset_timeout: int = 60,
    exclude: Optional[List[Type[Exception]]] = None
) -> pybreaker.CircuitBreaker:
    """Factory to create a configured Circuit Breaker."""
    return pybreaker.CircuitBreaker(
        fail_max=fail_max,
        reset_timeout=reset_timeout,
        name=name,
        listeners=[ObservabilityListener()],
        exclude=exclude or []
    )

# --- Global Breakers ---

# LLM Breaker: Semantic Layer
# Excludes: RateLimitError, AuthenticationError (Soft failures)
# Note: We import exceptions dynamically to avoid circular imports or import errors if optional deps missing.
_llm_excludes = []
try:
    from openai import RateLimitError, AuthenticationError, BadRequestError
    _llm_excludes.extend([RateLimitError, AuthenticationError, BadRequestError])
except ImportError:
    pass

LLM_BREAKER = create_breaker(
    name="LLM_BREAKER",
    fail_max=5,
    reset_timeout=60,
    exclude=_llm_excludes
)

# Vector Breaker: Retrieval Layer
VECTOR_BREAKER = create_breaker(
    name="VECTOR_BREAKER",
    fail_max=5,
    reset_timeout=30  # Faster recovery for infra blips
)

# Database Breaker: Execution Layer
DB_BREAKER = create_breaker(
    name="DB_BREAKER",
    fail_max=5,
    reset_timeout=30
)
