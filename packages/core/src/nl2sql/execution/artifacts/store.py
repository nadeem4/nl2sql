from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import polars as pl

from nl2sql.common.settings import settings
from nl2sql.execution.contracts import ArtifactRef
from nl2sql_adapter_sdk.contracts import ResultFrame

from .parquet import polars_to_result_frame, read_parquet, result_frame_to_polars, write_parquet

_PLACEHOLDER = re.compile(r"<([a-zA-Z0-9_]+)>")


@dataclass(frozen=True)
class ArtifactStoreConfig:
    backend: str
    base_uri: str
    path_template: str
    s3_bucket: Optional[str] = None
    s3_prefix: Optional[str] = None
    adls_account: Optional[str] = None
    adls_container: Optional[str] = None
    adls_connection_string: Optional[str] = None


class ArtifactStore:
    """Writes and reads Parquet result artifacts for every supported backend.

    Backends differ only in how the target URI is built; polars handles ``s3://``
    and ``abfs://`` natively, so the read/write path itself is shared.
    """

    def __init__(self, config: ArtifactStoreConfig) -> None:
        self.config = config

    def create_artifact_ref(self, frame: ResultFrame, metadata: Dict[str, str]) -> ArtifactRef:
        uri = self._build_uri(metadata)
        df = result_frame_to_polars(frame)
        write_parquet(df, uri, storage_options=self._storage_options())

        payload = {
            "columns": frame.columns,
            "row_count": frame.row_count,
            "path": uri,
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()

        return self._build_artifact_ref(
            uri=uri,
            frame=frame,
            content_hash=content_hash,
            bytes_written=self._bytes_written(uri, df),
            schema_version=metadata.get("schema_version"),
        )

    def read_result_frame(self, artifact: ArtifactRef) -> ResultFrame:
        return polars_to_result_frame(self.read_parquet(artifact))

    def read_parquet(self, artifact: ArtifactRef) -> pl.DataFrame:
        return read_parquet(artifact.uri, storage_options=self._storage_options())

    def _render_path(self, metadata: Dict[str, str]) -> str:
        def substitute(match: re.Match) -> str:
            key = match.group(1)
            value = metadata.get(key)
            if value in (None, ""):
                raise ValueError(
                    f"Cannot render artifact path template '{self.config.path_template}': "
                    f"no value for placeholder '<{key}>'. Provide '{key}' in the artifact metadata "
                    f"or remove it from RESULT_ARTIFACT_PATH_TEMPLATE."
                )
            return str(value)

        return _PLACEHOLDER.sub(substitute, self.config.path_template)

    def _build_uri(self, metadata: Dict[str, str]) -> str:
        backend = self.config.backend
        relative_path = self._render_path(metadata)

        if backend == "local":
            target = Path(self.config.base_uri) / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            return str(target.resolve())

        if backend == "s3":
            if not self.config.s3_bucket:
                raise ValueError(
                    "S3 artifact backend requires a bucket; set RESULT_ARTIFACT_S3_BUCKET."
                )
            prefix = (self.config.s3_prefix or "").strip("/")
            key = f"{prefix}/{relative_path}" if prefix else relative_path
            return f"s3://{self.config.s3_bucket}/{key}"

        if backend == "adls":
            if not self.config.adls_container:
                raise ValueError(
                    "ADLS artifact backend requires a container; set RESULT_ARTIFACT_ADLS_CONTAINER."
                )
            if not self.config.adls_account:
                raise ValueError(
                    "ADLS artifact backend requires a storage account; set RESULT_ARTIFACT_ADLS_ACCOUNT."
                )
            host = f"{self.config.adls_account}.dfs.core.windows.net"
            return f"abfs://{self.config.adls_container}@{host}/{relative_path}"

        raise ValueError(
            f"Unsupported artifact backend '{backend}'. Expected one of: local, s3, adls."
        )

    def _storage_options(self) -> Optional[Dict[str, Any]]:
        if self.config.backend == "adls" and self.config.adls_connection_string:
            return {"connection_string": self.config.adls_connection_string}
        return None

    def _bytes_written(self, uri: str, df: pl.DataFrame) -> int:
        if self.config.backend == "local":
            path = Path(uri)
            return path.stat().st_size if path.exists() else 0
        return df.estimated_size()

    def _build_artifact_ref(
        self,
        uri: str,
        frame: ResultFrame,
        content_hash: str,
        bytes_written: int,
        schema_version: Optional[str],
    ) -> ArtifactRef:
        return ArtifactRef(
            uri=uri,
            backend=self.config.backend,
            format="parquet",
            row_count=frame.row_count or len(frame.rows),
            columns=frame.columns,
            bytes=bytes_written,
            content_hash=content_hash,
            created_at=datetime.utcnow(),
            schema_version=schema_version,
            path_template=self.config.path_template,
        )


def build_artifact_store() -> ArtifactStore:
    return ArtifactStore(
        ArtifactStoreConfig(
            backend=settings.result_artifact_backend,
            base_uri=settings.result_artifact_base_uri,
            path_template=settings.result_artifact_path_template,
            s3_bucket=settings.result_artifact_s3_bucket,
            s3_prefix=settings.result_artifact_s3_prefix,
            adls_account=settings.result_artifact_adls_account,
            adls_container=settings.result_artifact_adls_container,
            adls_connection_string=settings.result_artifact_adls_connection_string,
        )
    )
