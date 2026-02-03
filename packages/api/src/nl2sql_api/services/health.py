from typing import Dict, Any, Optional
from nl2sql import NL2SQL


class HealthService:
    def __init__(self, engine: NL2SQL):
        self.engine = engine

    def health_check(self) -> dict:
        """Perform health check."""
        return {
            "success": True,
            "message": "NL2SQL API is running"
        }

    def readiness_check(self) -> dict:
        """Perform readiness check."""
        # Add actual readiness checks (database connections, etc.)
        return {
            "success": True,
            "message": "NL2SQL API is ready"
        }