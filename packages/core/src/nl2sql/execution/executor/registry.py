from __future__ import annotations

from typing import Dict, Optional, Set, Iterable

from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql.datasources import DatasourceRegistry

from .base import ExecutorService
from .sql_executor import SqlExecutorService
from nl2sql.common.logger import get_logger

logger = get_logger("executor_registry")


class ExecutorRegistry:
    """Registry and factory for executor services by capability."""

    def __init__(
        self,
        ds_registry: Optional[DatasourceRegistry] = None,
        register_defaults: bool = True,
    ) -> None:
        self._services: Dict[str, ExecutorService] = {}
        self.ds_registry = ds_registry
        if register_defaults and ds_registry is not None:
            self.register(DatasourceCapability.SUPPORTS_SQL, SqlExecutorService(ds_registry))

    def register(self, capability: DatasourceCapability | str, service: ExecutorService) -> None:
        key = capability.value if isinstance(capability, DatasourceCapability) else str(capability)
        self._services[key] = service

    def get_executor_by_capabilities(self, capabilities: Set[DatasourceCapability | str]) -> Optional[ExecutorService]:
        normalized = self._normalize_caps(capabilities)
        for cap in normalized:
            service = self._services.get(cap)
            if service is not None:
                return service
        return None
    
    def get_executor(self, ds_id: str) -> Optional[ExecutorService]:
        adapter = self.ds_registry.get_adapter(ds_id)  
        try:
            caps = adapter.capabilities() 
            return self.get_executor_by_capabilities(caps)

        except Exception as e:
            logger.error(f"Failed to get capabilities for datasource '{ds_id}'. {e}")
            return None

    def _normalize_caps(self, capabilities: Set[DatasourceCapability | str]) -> Set[str]:
        normalized: Set[str] = set()
        for cap in capabilities:
            if isinstance(cap, DatasourceCapability):
                normalized.add(cap.value)
            else:
                normalized.add(str(cap))
        return normalized
