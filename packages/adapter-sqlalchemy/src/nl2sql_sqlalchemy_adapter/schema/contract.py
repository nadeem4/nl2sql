
from datetime import datetime
import logging
from collections import defaultdict, OrderedDict
from typing import Dict, List, Optional
from .models import SchemaContract
import hashlib
import json

logger = logging.getLogger(__name__)

class SchemaContractStore:
    """Registry for schema contracts
    
    This registry is used to store schema contracts for different datasources.
    It stores the last `max_versions` schema contracts for each datasource.
    """
    def __init__(self, max_versions: int = 3):
        """Initialize the registry with a maximum number of versions to keep.

        _fingerprint_index: datasource_id -> fingerprint -> version
        _registry: datasource_id -> version -> schema_contract
        
        Args:
            max_versions (int): Maximum number of versions to keep.
        """
        self._registry: Dict[str, OrderedDict[str, SchemaContract]] = defaultdict(OrderedDict)
        self._fingerprint_index: Dict[str, Dict[str, str]] = defaultdict(dict)
        self._max_versions = max_versions


    def register(self, schema: SchemaContract) -> tuple[str, List[str]]:
        """Register a new schema contract."""

        fingerprint = self._generate_fingerprint(schema)
        version = self._check_schema_exists(schema.datasource_id, fingerprint)
        if version:
            logger.info(f"Schema for {schema.datasource_id} already exists with version {version}")
            return version 

        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        schema_version = f'{ts}_{fingerprint[:8]}'
        logger.info(f"Adding new schema for {schema.datasource_id} with version {schema_version}")        

        self._registry[schema.datasource_id][schema_version] = schema
        self._fingerprint_index[schema.datasource_id][fingerprint] = schema_version

        evicted_versions = self._evict_old_versions(schema.datasource_id)

        return schema_version, evicted_versions

    def get_all_versions(self, datasource_id: str) -> List[str]:
        """Get all versions of a schema contract."""
        if datasource_id not in self._registry:
            return []
        return list(self._registry[datasource_id].keys())

    def _generate_fingerprint(self, schema: SchemaContract) -> str:
        """Generate a fingerprint for a schema contract."""
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
                        for c in sorted(table.columns, key=lambda c: c.name)
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

        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _check_schema_exists(self, datasource_id: str, fingerprint: str) -> str:
        """Check if a schema contract exists."""
        if datasource_id not in self._fingerprint_index:
            return None
        
        return self._fingerprint_index[datasource_id].get(fingerprint)

    
    def get(self, datasource_id: str, schema_version: str) -> Optional[SchemaContract]:
        """Get a schema contract by version."""
        return self._registry.get(datasource_id, {}).get(schema_version)

    def get_latest(self, datasource_id: str) -> Optional[SchemaContract]:
        """Get the latest schema contract."""
        versions = self._registry.get(datasource_id)
        if not versions:
            return None
        return next(reversed(versions.values()))

    def _evict_old_versions(self, datasource_id: str) -> List[str]:
        versions = self._registry[datasource_id]
        fp_index = self._fingerprint_index[datasource_id]
        evicted_versions = []

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
 
        
