from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
import sqlite3
from typing import List, Optional, Tuple

from nl2sql_adapter_sdk.schema import (
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableMetadata,
)

from .protocol import generate_schema_fingerprint

logger = logging.getLogger(__name__)


class SqliteSchemaStore:
    """SQLite-backed schema store with versioning and per-table access."""

    def __init__(self, path: Path, max_versions: int = 3):
        self._path = path
        self._max_versions = max_versions
        self._connection = self._connect()
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self._path))
        connection.execute("PRAGMA journal_mode=WAL;")
        return connection

    def _initialize_schema(self) -> None:
        cursor = self._connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_snapshots (
                datasource_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                contract_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (datasource_id, schema_version)
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_schema_snapshots_fingerprint
            ON schema_snapshots (datasource_id, fingerprint);
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_schema_snapshots_created_at
            ON schema_snapshots (datasource_id, created_at);
            """
        )
        self._connection.commit()

    def register_snapshot(self, snapshot: SchemaSnapshot) -> Tuple[str, List[str]]:
        fingerprint = generate_schema_fingerprint(snapshot.contract)
        existing_version = self._get_version_by_fingerprint(
            snapshot.contract.datasource_id, fingerprint
        )
        if existing_version:
            logger.info(
                "Schema for %s already exists with version %s",
                snapshot.contract.datasource_id,
                existing_version,
            )
            return existing_version, []

        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        schema_version = f"{ts}_{fingerprint[:8]}"
        created_at = int(datetime.utcnow().timestamp())

        contract_json = json.dumps(snapshot.contract.model_dump(mode="json"))
        metadata_json = json.dumps(snapshot.metadata.model_dump(mode="json"))

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO schema_snapshots (
                    datasource_id,
                    schema_version,
                    fingerprint,
                    contract_json,
                    metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    snapshot.contract.datasource_id,
                    schema_version,
                    fingerprint,
                    contract_json,
                    metadata_json,
                    created_at,
                ),
            )

        evicted_versions = self._evict_old_versions(snapshot.contract.datasource_id)
        return schema_version, evicted_versions

    def get_snapshot(
        self, datasource_id: str, schema_version: str
    ) -> Optional[SchemaSnapshot]:
        row = self._connection.execute(
            """
            SELECT contract_json, metadata_json
            FROM schema_snapshots
            WHERE datasource_id = ? AND schema_version = ?;
            """,
            (datasource_id, schema_version),
        ).fetchone()
        if not row:
            return None
        contract = SchemaContract.model_validate(json.loads(row[0]))
        metadata = SchemaMetadata.model_validate(json.loads(row[1]))
        return SchemaSnapshot(contract=contract, metadata=metadata)

    def get_latest_snapshot(self, datasource_id: str) -> Optional[SchemaSnapshot]:
        latest_version = self.get_latest_version(datasource_id)
        if not latest_version:
            return None
        return self.get_snapshot(datasource_id, latest_version)

    def get_latest_version(self, datasource_id: str) -> Optional[str]:
        row = self._connection.execute(
            """
            SELECT schema_version
            FROM schema_snapshots
            WHERE datasource_id = ?
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (datasource_id,),
        ).fetchone()
        return row[0] if row else None

    def list_versions(self, datasource_id: str) -> List[str]:
        rows = self._connection.execute(
            """
            SELECT schema_version
            FROM schema_snapshots
            WHERE datasource_id = ?
            ORDER BY created_at ASC;
            """,
            (datasource_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def get_table_contract(
        self,
        datasource_id: str,
        schema_version: str,
        table_key: str,
    ) -> Optional[TableContract]:
        snapshot = self.get_snapshot(datasource_id, schema_version)
        if not snapshot:
            return None
        return snapshot.contract.tables.get(table_key)

    def get_table_metadata(
        self,
        datasource_id: str,
        schema_version: str,
        table_key: str,
    ) -> Optional[TableMetadata]:
        snapshot = self.get_snapshot(datasource_id, schema_version)
        if not snapshot:
            return None
        return snapshot.metadata.tables.get(table_key)

    def _get_version_by_fingerprint(
        self, datasource_id: str, fingerprint: str
    ) -> Optional[str]:
        row = self._connection.execute(
            """
            SELECT schema_version
            FROM schema_snapshots
            WHERE datasource_id = ? AND fingerprint = ?
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (datasource_id, fingerprint),
        ).fetchone()
        return row[0] if row else None

    def _evict_old_versions(self, datasource_id: str) -> List[str]:
        rows = self._connection.execute(
            """
            SELECT schema_version
            FROM schema_snapshots
            WHERE datasource_id = ?
            ORDER BY created_at ASC;
            """,
            (datasource_id,),
        ).fetchall()
        versions = [row[0] for row in rows]
        if len(versions) <= self._max_versions:
            return []

        evicted_versions = versions[: len(versions) - self._max_versions]
        with self._connection:
            self._connection.executemany(
                """
                DELETE FROM schema_snapshots
                WHERE datasource_id = ? AND schema_version = ?;
                """,
                [(datasource_id, version) for version in evicted_versions],
            )

        for version in evicted_versions:
            logger.info(
                "Evicted old schema version for %s: %s",
                datasource_id,
                version,
            )

        return evicted_versions
