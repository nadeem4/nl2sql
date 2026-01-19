from typing import Optional
from .adapter import BaseSQLAlchemyAdapter
from .schema import SchemaContractStore, SchemaMetadataStore
from .schema.models import SchemaContract, SchemaMetadata

class SchemaRefreshOrchestrator:
    def __init__(self, contract_store: SchemaContractStore, metadata_store: SchemaMetadataStore):
        self._contract_store = contract_store
        self._metadata_store = metadata_store

    def refresh(self, adapter: BaseSQLAlchemyAdapter):
        snapshot = adapter.fetch_schema_snapshot()
        version, evicted_versions = self._contract_store.register(snapshot.contract)
        self._metadata_store.set(adapter.datasource_id, version, snapshot.metadata)
        for evicted_version in evicted_versions:
            self._metadata_store.delete(adapter.datasource_id, evicted_version)
        return version, evicted_versions

    def get_schema_contract(self, ds_id: str, schema_version: str) -> Optional[SchemaContract]:
        return self._contract_store.get(ds_id, schema_version)

    def get_schema_metadata(self, ds_id: str, schema_version: str) -> Optional[SchemaMetadata]:
        return self._metadata_store.get(ds_id, schema_version)