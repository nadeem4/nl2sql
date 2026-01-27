from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import datetime
import hashlib
import json
import logging
from typing import Dict, List, Optional, Protocol, Tuple

from nl2sql_adapter_sdk.schema import (
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
)

logger = logging.getLogger(__name__)


class SchemaContractStore:
    """Registry for schema contracts by datasource/version."""

    def __init__(self, max_versions: int = 3):
        self._registry: Dict[str, OrderedDict[str, SchemaContract]] = defaultdict(
            OrderedDict
        )
        self._fingerprint_index: Dict[str, Dict[str, str]] = defaultdict(dict)
        self._max_versions = max_versions

    def register(self, schema: SchemaContract) -> tuple[str, List[str]]:
        fingerprint = self._generate_fingerprint(schema)
        version = self._check_schema_exists(schema.datasource_id, fingerprint)
        if version:
            logger.info(
                "Schema for %s already exists with version %s",
                schema.datasource_id,
                version,
            )
            return version, []

        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        schema_version = f"{ts}_{fingerprint[:8]}"
        logger.info(
            "Adding new schema for %s with version %s",
            schema.datasource_id,
            schema_version,
        )

        self._registry[schema.datasource_id][schema_version] = schema
        self._fingerprint_index[schema.datasource_id][fingerprint] = schema_version

        evicted_versions = self._evict_old_versions(schema.datasource_id)
        return schema_version, evicted_versions

    def get_all_versions(self, datasource_id: str) -> List[str]:
        if datasource_id not in self._registry:
            return []
        return list(self._registry[datasource_id].keys())

    def _generate_fingerprint(self, schema: SchemaContract) -> str:
        payload = {
            "datasource_id": schema.datasource_id,
            "engine_type": schema.engine_type,
            "tables": {
                table_key: {
                    "columns": [
                        {
                            "name": c.name,
                            "type": c.data_type,
                            "nullable": c.is_nullable,
                            "pk": c.is_primary_key,
                        }
                        for c in sorted(table.columns.values(), key=lambda c: c.name)
                    ],
                    "fks": [
                        {
                            "cols": sorted(fk.constrained_columns),
                            "ref_table": fk.referred_table,
                            "ref_cols": sorted(fk.referred_columns),
                        }
                        for fk in sorted(
                            table.foreign_keys,
                            key=lambda fk: (
                                fk.referred_table,
                                sorted(fk.constrained_columns),
                            ),
                        )
                    ],
                }
                for table_key, table in sorted(schema.tables.items())
            },
        }

        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _check_schema_exists(self, datasource_id: str, fingerprint: str) -> Optional[str]:
        if datasource_id not in self._fingerprint_index:
            return None
        return self._fingerprint_index[datasource_id].get(fingerprint)

    def get(self, datasource_id: str, schema_version: str) -> Optional[SchemaContract]:
        return self._registry.get(datasource_id, {}).get(schema_version)

    def get_latest(self, datasource_id: str) -> Optional[SchemaContract]:
        versions = self._registry.get(datasource_id)
        if not versions:
            return None
        return next(reversed(versions.values()))

    def get_latest_version(self, datasource_id: str) -> Optional[str]:
        versions = self._registry.get(datasource_id)
        if not versions:
            return None
        return next(reversed(versions.keys()))

    def _evict_old_versions(self, datasource_id: str) -> List[str]:
        versions = self._registry[datasource_id]
        fp_index = self._fingerprint_index[datasource_id]
        evicted_versions: List[str] = []

        while len(versions) > self._max_versions:
            evicted_version, evicted_schema = versions.popitem(last=False)
            evicted_versions.append(evicted_version)

            evicted_fp = self._generate_fingerprint(evicted_schema)
            fp_index.pop(evicted_fp, None)

            logger.info(
                "Evicted old schema version for %s: %s",
                datasource_id,
                evicted_version,
            )

        return evicted_versions


class SchemaMetadataStore:
    def __init__(self):
        self._store: Dict[str, Dict[str, SchemaMetadata]] = defaultdict(dict)

    def get(self, datasource_id: str, schema_version: str) -> Optional[SchemaMetadata]:
        return self._store.get(datasource_id, {}).get(schema_version)

    def register(self, schema_version: str, schema: SchemaMetadata):
        self._store[schema.datasource_id][schema_version] = schema

    def delete(self, datasource_id: str, schema_version: str):
        self._store.get(datasource_id, {}).pop(schema_version, None)


class SchemaStore(Protocol):
    """Unified interface for schema snapshot storage backends."""

    def register_snapshot(self, snapshot: SchemaSnapshot) -> Tuple[str, List[str]]:
        ...

    def get_snapshot(self, datasource_id: str, schema_version: str) -> Optional[SchemaSnapshot]:
        ...

    def get_latest_snapshot(self, datasource_id: str) -> Optional[SchemaSnapshot]:
        ...

    def get_latest_version(self, datasource_id: str) -> Optional[str]:
        ...

    def list_versions(self, datasource_id: str) -> List[str]:
        ...

    def get_table_contract(
        self,
        datasource_id: str,
        schema_version: str,
        table_key: str,
    ) -> Optional[TableContract]:
        ...

    def get_table_metadata(
        self,
        datasource_id: str,
        schema_version: str,
        table_key: str,
    ) -> Optional[TableMetadata]:
        ...


class InMemorySchemaStore:
    """In-memory schema store with versioning and per-table access."""

    def __init__(self, max_versions: int = 3):
        self._contracts = SchemaContractStore(max_versions=max_versions)
        self._metadata = SchemaMetadataStore()

    def register_snapshot(self, snapshot: SchemaSnapshot) -> Tuple[str, List[str]]:
        schema_version, evicted_versions = self._contracts.register(snapshot.contract)
        self._metadata.register(schema_version, snapshot.metadata)

        for evicted_version in evicted_versions:
            self._metadata.delete(snapshot.contract.datasource_id, evicted_version)

        return schema_version, evicted_versions

    def get_snapshot(self, datasource_id: str, schema_version: str) -> Optional[SchemaSnapshot]:
        contract = self._contracts.get(datasource_id, schema_version)
        metadata = self._metadata.get(datasource_id, schema_version)
        if not contract or not metadata:
            return None
        return SchemaSnapshot(contract=contract, metadata=metadata)

    def get_latest_snapshot(self, datasource_id: str) -> Optional[SchemaSnapshot]:
        latest_version = self._contracts.get_latest_version(datasource_id)
        if not latest_version:
            return None
        return self.get_snapshot(datasource_id, latest_version)

    def get_latest_version(self, datasource_id: str) -> Optional[str]:
        return self._contracts.get_latest_version(datasource_id)

    def list_versions(self, datasource_id: str) -> List[str]:
        return self._contracts.get_all_versions(datasource_id)

    def get_table_contract(
        self,
        datasource_id: str,
        schema_version: str,
        table_key: str,
    ) -> Optional[TableContract]:
        contract = self._contracts.get(datasource_id, schema_version)
        if not contract:
            return None
        return contract.tables.get(table_key)

    def get_table_metadata(
        self,
        datasource_id: str,
        schema_version: str,
        table_key: str,
    ) -> Optional[TableMetadata]:
        metadata = self._metadata.get(datasource_id, schema_version)
        if not metadata:
            return None
        return metadata.tables.get(table_key)


def build_schema_store(backend: str, max_versions: int) -> SchemaStore:
    backend_key = (backend or "memory").lower()
    if backend_key == "memory":
        return InMemorySchemaStore(max_versions=max_versions)
    raise ValueError(f"Unsupported schema store backend: {backend}")
