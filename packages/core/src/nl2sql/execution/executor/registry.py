from __future__ import annotations

from typing import Dict, Optional, Set, Iterable

from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql.datasources import DatasourceRegistry

from .base import ExecutorService
from .sql_executor import SqlExecutorService


class ExecutorRegistry:
    """Registry and factory for executor services by capability."""

    def __init__(
        self,
        ds_registry: Optional[DatasourceRegistry] = None,
        register_defaults: bool = True,
    ) -> None:
        self._services: Dict[str, ExecutorService] = {}
        if register_defaults and ds_registry is not None:
            self.register(DatasourceCapability.SUPPORTS_SQL, SqlExecutorService(ds_registry))

    def register(self, capability: DatasourceCapability | str, service: ExecutorService) -> None:
        key = capability.value if isinstance(capability, DatasourceCapability) else str(capability)
        self._services[key] = service

    def get_executor(self, capabilities: Iterable[DatasourceCapability | str]) -> Optional[ExecutorService]:
        normalized = self._normalize_caps(capabilities)
        for cap in normalized:
            service = self._services.get(cap)
            if service is not None:
                return service
        return None

    def _normalize_caps(self, capabilities: Iterable[DatasourceCapability | str]) -> Set[str]:
        normalized: Set[str] = set()
        for cap in capabilities:
            if isinstance(cap, DatasourceCapability):
                normalized.add(cap.value)
            else:
                normalized.add(str(cap))
        return normalized
