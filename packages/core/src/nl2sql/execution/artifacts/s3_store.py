from __future__ import annotations

import hashlib
import json
from typing import Dict

from nl2sql_adapter_sdk.contracts import ResultFrame

from .base import ArtifactStore
from .parquet import result_frame_to_polars, write_parquet_polars, read_parquet_polars


class S3ArtifactStore(ArtifactStore):
    def write_result_frame(self, frame: ResultFrame, metadata: Dict[str, str]):
        if not self.config.s3_bucket:
            raise ValueError("S3 bucket not configured for artifact store.")
        relative_path = self._render_path(metadata)
        prefix = self.config.s3_prefix or ""
        key = f"{prefix.rstrip('/')}/{relative_path}".lstrip("/")

        df = result_frame_to_polars(frame)
        uri = f"s3://{self.config.s3_bucket}/{key}"
        write_parquet_polars(df, uri)
        bytes_written = getattr(df, "estimated_size", lambda: 0)()

        payload = {
            "columns": [c.name for c in frame.columns],
            "row_count": frame.row_count,
            "key": key,
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()

        return self._build_artifact_ref(
            uri=uri,
            frame=frame,
            content_hash=content_hash,
            bytes_written=bytes_written,
            schema_version=metadata.get("schema_version"),
        )

    def read_result_frame(self, artifact):
        if not artifact.uri.startswith("s3://"):
            raise ValueError(f"Unsupported S3 URI: {artifact.uri}")
        return read_parquet_polars(artifact.uri)
