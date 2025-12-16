from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Any, Dict
import time

class ErrorSeverity(str, Enum):
    WARNING = "warning"   # Non-blocking, recovered (e.g. decomposition fallback)
    ERROR = "error"       # Blocking for a branch, but graph continues (e.g. 1/3 DBs failed)
    CRITICAL = "critical" # Pipeline crash (e.g. Config missing)

@dataclass
class PipelineError:
    node: str
    message: str
    severity: ErrorSeverity
    error_code: str = "UNKNOWN"
    details: Optional[str] = None
    stack_trace: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node": self.node,
            "message": self.message,
            "severity": self.severity.value,
            "error_code": self.error_code,
            "details": self.details,
            "timestamp": self.timestamp
        }
