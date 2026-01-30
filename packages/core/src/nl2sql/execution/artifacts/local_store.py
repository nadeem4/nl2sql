from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict

import polars as pl
from nl2sql_adapter_sdk.contracts import ResultFrame

from .base import ArtifactStore
from .parquet import read_parquet, table_to_result_frame, result_frame_to_polars


class LocalArtifactStore(ArtifactStore):

    def create_artifact_ref(self, frame: ResultFrame, metadata: Dict[str, str]):
        request_id = metadata.get("request_id")
        if not request_id:
            raise ValueError("Missing 'request_id' in metadata for upload path generation.")
        
        tenant_id = metadata.get("tenant_id")
        if not tenant_id:
            raise ValueError("Missing 'tenant_id' in metadata for upload path generation.")
        
        upload_path = self.get_upload_path(request_id, tenant_id)
    
        df = result_frame_to_polars(frame)
        df.write_parquet(upload_path)

        payload = {
            "columns": frame.columns,
            "row_count": frame.row_count,
            "path": str(upload_path),
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()

        bytes_written = Path(upload_path).stat().st_size if Path(upload_path).exists() else 0
        return self._build_artifact_ref(
            uri=upload_path,
            frame=frame,
            content_hash=content_hash,
            bytes_written=bytes_written,
            schema_version=metadata.get("schema_version"),
        )

    def read_result_frame(self, artifact):
        table = read_parquet(artifact.uri)
        return table_to_result_frame(table)
    
    def read_parquet(self, artifact) -> pl.DataFrame:
        return pl.read_parquet(artifact.uri)
    
    def get_upload_path(self, request_id: str, tenant_id: str) -> str:
        base_path = Path(self.config.base_uri)
        target_path = base_path / tenant_id / f"{request_id}.parquet"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        return str(target_path.resolve())
