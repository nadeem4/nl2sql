from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict

from nl2sql_adapter_sdk.contracts import ResultFrame

from .base import ArtifactStore
from .parquet import read_parquet, table_to_result_frame, result_frame_to_polars, write_parquet_polars


class LocalArtifactStore(ArtifactStore):
    def write_result_frame(self, frame: ResultFrame, metadata: Dict[str, str]):
        relative_path = self._render_path(metadata)
        base_path = Path(self.config.base_uri)
        target_path = base_path / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        df = result_frame_to_polars(frame)
        write_parquet_polars(df, str(target_path))

        payload = {
            "columns": [c.name for c in frame.columns],
            "row_count": frame.row_count,
            "path": str(relative_path),
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()

        bytes_written = target_path.stat().st_size if target_path.exists() else 0
        return self._build_artifact_ref(
            uri=str(target_path.resolve()),
            frame=frame,
            content_hash=content_hash,
            bytes_written=bytes_written,
            schema_version=metadata.get("schema_version"),
        )

    def read_result_frame(self, artifact):
        table = read_parquet(artifact.uri)
        return table_to_result_frame(table)
