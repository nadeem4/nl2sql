from __future__ import annotations

import hashlib
import json
from typing import List, Optional, Protocol, Tuple

from nl2sql_adapter_sdk.schema import (
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
)


def generate_schema_fingerprint(schema: SchemaContract) -> str:
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


class SchemaStore(Protocol):
    """Unified interface for schema snapshot storage backends."""

    def register_snapshot(self, snapshot: SchemaSnapshot) -> Tuple[str, List[str]]:
        ...

    def get_snapshot(
        self, datasource_id: str, schema_version: str
    ) -> Optional[SchemaSnapshot]:
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
