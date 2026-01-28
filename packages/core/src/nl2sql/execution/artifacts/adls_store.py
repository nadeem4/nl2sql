from __future__ import annotations

import hashlib
import json
from typing import Dict

from azure.storage.blob import BlobServiceClient

from nl2sql_adapter_sdk.contracts import ResultFrame

from .base import ArtifactStore
from .parquet import result_frame_to_polars, write_parquet_polars, read_parquet_polars


class AdlsArtifactStore(ArtifactStore):
    def _get_client(self) -> BlobServiceClient:
        if self.config.adls_connection_string:
            return BlobServiceClient.from_connection_string(self.config.adls_connection_string)
        if not self.config.adls_account:
            raise ValueError("ADLS account is not configured.")
        account_url = f"https://{self.config.adls_account}.blob.core.windows.net"
        return BlobServiceClient(account_url=account_url)

    def write_result_frame(self, frame: ResultFrame, metadata: Dict[str, str]):
        if not self.config.adls_container:
            raise ValueError("ADLS container not configured for artifact store.")
        relative_path = self._render_path(metadata)
        container = self.config.adls_container

        df = result_frame_to_polars(frame)
        uri = f"abfs://{container}@{self.config.adls_account}.dfs.core.windows.net/{relative_path}"
        storage_options = None
        if self.config.adls_connection_string:
            storage_options = {"connection_string": self.config.adls_connection_string}
        write_parquet_polars(df, uri, storage_options=storage_options)
        bytes_written = getattr(df, "estimated_size", lambda: 0)()

        payload = {
            "columns": [c.name for c in frame.columns],
            "row_count": frame.row_count,
            "blob": relative_path,
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
        if not artifact.uri.startswith("abfs://"):
            raise ValueError(f"Unsupported ADLS URI: {artifact.uri}")
        storage_options = None
        if self.config.adls_connection_string:
            storage_options = {"connection_string": self.config.adls_connection_string}
        return read_parquet_polars(artifact.uri, storage_options=storage_options)
