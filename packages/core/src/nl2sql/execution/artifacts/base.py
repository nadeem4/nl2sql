from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

from abc import ABC, abstractmethod

import polars as pl
from nl2sql.common.settings import settings
from nl2sql.execution.contracts import ArtifactRef
from nl2sql_adapter_sdk.contracts import ResultFrame


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


class ArtifactStore(ABC):
    def __init__(self, config: ArtifactStoreConfig) -> None:
        self.config = config

    @abstractmethod
    def create_artifact_ref(self, frame: ResultFrame, metadata: Dict[str, str]) -> ArtifactRef:
        raise NotImplementedError

    @abstractmethod
    def read_result_frame(self, artifact: ArtifactRef) -> ResultFrame:
        raise NotImplementedError
    
    @abstractmethod
    def read_parquet(self, artifact: ArtifactRef) -> pl.DataFrame:
        return NotImplementedError

    @abstractmethod
    def get_upload_path(self, request_id: str, tenant_id: str) -> str:
        return NotImplementedError

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
    config = ArtifactStoreConfig(
        backend=settings.result_artifact_backend,
        base_uri=settings.result_artifact_base_uri,
        path_template=settings.result_artifact_path_template,
        s3_bucket=settings.result_artifact_s3_bucket,
        s3_prefix=settings.result_artifact_s3_prefix,
        adls_account=settings.result_artifact_adls_account,
        adls_container=settings.result_artifact_adls_container,
        adls_connection_string=settings.result_artifact_adls_connection_string,
    )

    if config.backend == "s3":
        from .s3_store import S3ArtifactStore

        return S3ArtifactStore(config)
    if config.backend == "adls":
        from .adls_store import AdlsArtifactStore

        return AdlsArtifactStore(config)
    from .local_store import LocalArtifactStore

    return LocalArtifactStore(config)
