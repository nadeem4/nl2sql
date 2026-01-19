from typing import Dict, Optional
from .models import SchemaMetadata
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

class SchemaMetadataStore:

    def __init__(self):
        self._store: Dict[str, Dict[str, SchemaMetadata]] = defaultdict(dict)

    def get(self, datasource_id: str, schema_version: str) -> Optional[SchemaMetadata]:
        return self._store.get(datasource_id, {}).get(schema_version)

    def register(self, schema_version: str, schema: SchemaMetadata):
        self._store[schema.datasource_id][schema_version] = schema

    def delete(self, datasource_id: str, schema_version: str):
        self._store.get(datasource_id, {}).pop(schema_version, None)

