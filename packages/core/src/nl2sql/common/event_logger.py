import logging
import json
import os
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional
from datetime import datetime
from nl2sql.common.settings import settings

class EventLogger:
    """Persistent audit logger for high-value AI events.
    
    Writes structured JSON events to a dedicated log file, separate from
    application debug logs. Used for forensic analysis and "Time Travel" debugging.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("nl2sql.audit")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False  # Do not bubble up to root logger (avoid stdout spam)
        
        # Ensure handlers are set up (singleton-ish check)
        if not self.logger.handlers:
            log_path = getattr(settings, "audit_log_path", "logs/audit_events.log")
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            
            # 10MB per file, max 5 backup files
            handler = RotatingFileHandler(
                log_path, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
            )
            
            # Use specific JSON formatter for audit events
            formatter = logging.Formatter("%(message)s")
            handler.setFormatter(formatter)
            
            self.logger.addHandler(handler)

    def log_event(
        self, 
        event_type: str, 
        payload: Dict[str, Any], 
        trace_id: Optional[str] = None, 
        tenant_id: Optional[str] = None
    ):
        """Logs a structured event to the audit log.
        
        Args:
            event_type: Category of event (e.g., 'llm_interaction', 'security_violation')
            payload: The event data dictionary.
            trace_id: Correlation ID.
            tenant_id: Tenant/Customer ID.
        """
        
        sensitive_keys = {"api_key", "password", "secret", "authorization"}
        cleaned_payload = self._redact(payload, sensitive_keys)
        
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "data": cleaned_payload
        }
        
        self.logger.info(json.dumps(event))

    def _redact(self, data: Any, keys_to_redact: set) -> Any:
        """Recursively redact sensitive keys from dictionary.

        Args:
            data: Input data (dict, list, or primitive).
            keys_to_redact: Set of lowercase keys to match and redact.

        Returns:
            The sanitized data structure with sensitive values replaced by '***REDACTED***'.
        """
        if isinstance(data, dict):
            return {
                k: ("***REDACTED***" if k.lower() in keys_to_redact else self._redact(v, keys_to_redact))
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [self._redact(item, keys_to_redact) for item in data]
        else:
            return data

# Global instance
event_logger = EventLogger()
