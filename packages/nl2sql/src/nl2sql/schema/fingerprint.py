"""A schema's structural fingerprint: tables, columns, types, keys. Statistics and descriptions are left out."""
from __future__ import annotations

import hashlib
import json

from nl2sql_sdk.schema import SchemaContract


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
                        # ``full_name``, not the model: TableRef defines no
                        # ordering, so sorting by it raised TypeError as soon as
                        # a table had two foreign keys and no snapshot could be
                        # registered. Tables with 0 or 1 never compare, so
                        # fingerprints that already worked are unchanged.
                        key=lambda fk: (
                            fk.referred_table.full_name,
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
