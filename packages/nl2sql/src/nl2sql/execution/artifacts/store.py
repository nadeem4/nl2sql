from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import polars as pl

from nl2sql.common.settings import settings
from nl2sql.execution.contracts import ArtifactRef
from nl2sql_sdk.contracts import ResultFrame

from .parquet import (
    polars_to_result_frame,
    read_parquet,
    result_frame_to_polars,
    scan_parquet,
    sink_parquet,
    write_parquet,
)

_PLACEHOLDER = re.compile(r"<([a-zA-Z0-9_]+)>")

# Placeholders that tell one sub-query's artifact from another's in the same run.
_UNIQUE_PLACEHOLDERS = {"sub_query_id", "dag_node_id"}


# Where a result is written, under the store's folder.
DEFAULT_PATH_TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"


@dataclass(frozen=True)
class ArtifactStoreConfig:
    """``base_uri`` is the results folder, ``<storage.path>/results/`` for an engine."""

    base_uri: str
    path_template: str = DEFAULT_PATH_TEMPLATE


class ArtifactStore:
    """Writes and reads Parquet result artifacts under one local folder.

    Remote stores (S3, ADLS) were removed in 0.5; the Phase 2 results-store
    interface brings them back.
    """

    def __init__(self, config: ArtifactStoreConfig) -> None:
        self.config = config

    def create_artifact_ref(self, frame: ResultFrame, metadata: Dict[str, str]) -> ArtifactRef:
        return self.write_batches([frame], metadata)

    def write_batches(self, frames: Iterable[ResultFrame], metadata: Dict[str, str]) -> ArtifactRef:
        """Writes a result that arrives a batch at a time to one parquet artifact.

        One batch is written as it always was. From the second on, each batch
        is written to its own part file in a temporary folder as it arrives,
        and the parts are then streamed into the artifact by polars
        (``vertical_relaxed``, so a column that is empty in one batch and text
        in the next is text). At most one batch is held in memory. If
        ``frames`` raises, nothing is written and the parts are removed.
        """
        template = self._template_for(metadata)
        uri = self._build_uri(template, metadata)
        columns: Optional[List[str]] = None
        row_count = 0
        first: Optional[pl.DataFrame] = None
        spill_dir: Optional[str] = None
        parts: List[Path] = []
        try:
            for frame in frames:
                if columns is None:
                    columns = list(frame.columns)
                row_count += frame.row_count or len(frame.rows)
                df = result_frame_to_polars(frame)
                if first is None and spill_dir is None:
                    first = df
                    continue
                if spill_dir is None:
                    spill_dir = tempfile.mkdtemp(prefix="nl2sql-batches-")
                    parts.append(self._spill(first, spill_dir, 0))
                    first = None
                parts.append(self._spill(df, spill_dir, len(parts)))

            if parts:
                combined = pl.concat([pl.scan_parquet(p) for p in parts], how="vertical_relaxed")
                sink_parquet(combined, uri)
                estimated = sum(p.stat().st_size for p in parts)
            else:
                df = first if first is not None else pl.DataFrame()
                write_parquet(df, uri)
                estimated = df.estimated_size()
        finally:
            if spill_dir is not None:
                shutil.rmtree(spill_dir, ignore_errors=True)

        return self._artifact_ref(uri, template, columns or [], row_count, estimated, metadata)

    def write_lazy(self, frame: pl.LazyFrame, metadata: Dict[str, str]) -> ArtifactRef:
        """Writes a lazy result (the aggregator's combined result) to its own artifact.

        polars streams the plan into the file; the row count and columns are
        then read from the file's metadata, not by loading it.
        """
        template = self._template_for(metadata)
        uri = self._build_uri(template, metadata)
        sink_parquet(frame.lazy(), uri)
        written = scan_parquet(uri)
        row_count = int(written.select(pl.len()).collect().item())
        columns = written.collect_schema().names()
        return self._artifact_ref(uri, template, columns, row_count, 0, metadata)

    @staticmethod
    def run_metadata(tenant_id: str, request_id: str, name: str) -> Dict[str, str]:
        """The metadata a run's own files (a saved result, the manifest) are rendered with.

        ``name`` stands in for the sub-query and DAG node id: ``result_<node_id>``
        for a saved result, ``manifest`` for the manifest. These files have no
        subgraph or schema version, so a template naming them gets
        ``aggregator`` and ``result``.
        """
        return {
            "tenant_id": tenant_id,
            "request_id": request_id,
            "sub_query_id": name,
            "dag_node_id": name,
            "subgraph_name": "aggregator",
            "schema_version": "result",
        }

    def write_manifest(self, tenant_id: str, request_id: str, manifest: Any) -> str:
        """Writes a run's result manifest (a pydantic model) as JSON beside its results."""
        uri = self._manifest_uri(tenant_id, request_id, create=True)
        Path(uri).write_text(manifest.model_dump_json(), encoding="utf-8")
        return uri

    def read_manifest(self, tenant_id: str, request_id: str) -> Optional[Dict[str, Any]]:
        """A run's result manifest as a dict, or None when there is none."""
        uri = self._manifest_uri(tenant_id, request_id, create=False)
        try:
            text = Path(uri).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return json.loads(text)

    def scan_result(self, tenant_id: str, request_id: str, node_id: str) -> Optional[pl.LazyFrame]:
        """A run's saved result as a polars scan, or None when the file is missing.

        The path is rendered from the template, exactly as the aggregator wrote it.
        """
        metadata = self.run_metadata(tenant_id, request_id, f"result_{node_id}")
        uri = self._build_uri(self._template_for(metadata), metadata, create=False)
        if not Path(uri).is_file():
            return None
        return scan_parquet(uri)

    def _manifest_uri(self, tenant_id: str, request_id: str, create: bool) -> str:
        """The result template rendered with ``manifest`` as the name, as a ``.json`` file.

        The default template gives ``<tenant_id>/<request_id>/manifest.json``.
        """
        metadata = self.run_metadata(tenant_id, request_id, "manifest")
        uri = self._build_uri(self._template_for(metadata), metadata, create=create)
        stem = uri[: -len(".parquet")] if uri.endswith(".parquet") else uri
        return f"{stem}.json"

    def read_result_frame(self, artifact: ArtifactRef) -> ResultFrame:
        return polars_to_result_frame(self.read_parquet(artifact))

    def read_parquet(self, artifact: ArtifactRef) -> pl.DataFrame:
        return read_parquet(artifact.uri)

    def scan_parquet(self, artifact: ArtifactRef) -> pl.LazyFrame:
        """The artifact as a polars scan: nothing is read until it is collected."""
        return scan_parquet(artifact.uri)

    @staticmethod
    def _spill(df: pl.DataFrame, folder: str, index: int) -> Path:
        path = Path(folder) / f"part-{index:05d}.parquet"
        df.write_parquet(path)
        return path

    def _artifact_ref(
        self,
        uri: str,
        template: str,
        columns: List[str],
        row_count: int,
        estimated_bytes: int,
        metadata: Dict[str, str],
    ) -> ArtifactRef:
        payload = {
            "columns": columns,
            "row_count": row_count,
            "path": uri,
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        return ArtifactRef(
            uri=uri,
            backend="local",
            format="parquet",
            row_count=row_count,
            columns=columns,
            bytes=self._bytes_written(uri, estimated_bytes),
            content_hash=content_hash,
            created_at=datetime.utcnow(),
            schema_version=metadata.get("schema_version"),
            sub_query_id=metadata.get("sub_query_id"),
            path_template=template,
        )

    def _template_for(self, metadata: Dict[str, str]) -> str:
        """The template to render: the configured one, made unique per sub-query.

        Every sub-query of a run shares its trace id (``request_id``), so a
        template that names neither ``<sub_query_id>`` nor ``<dag_node_id>``
        would give them all one file: written side by side it is corrupt,
        written in turn it holds only the last result. Such a template gets
        ``-<sub_query_id>`` added to its file name, before the extension.
        """
        template = self.config.path_template
        if not metadata.get("sub_query_id") or _UNIQUE_PLACEHOLDERS & set(_PLACEHOLDER.findall(template)):
            return template
        head, sep, name = template.rpartition("/")
        stem, dot, ext = name.rpartition(".")
        if not dot or not stem:
            stem, ext = name, ""
        suffixed = f"{stem}-<sub_query_id>" + (f".{ext}" if ext else "")
        return f"{head}{sep}{suffixed}"

    def _render_path(self, template: str, metadata: Dict[str, str]) -> str:
        def substitute(match: re.Match) -> str:
            key = match.group(1)
            value = metadata.get(key)
            if value in (None, ""):
                raise ValueError(
                    f"Cannot render artifact path template '{template}': "
                    f"no value for placeholder '<{key}>'. Provide '{key}' in the artifact metadata "
                    f"or remove it from the path template."
                )
            return str(value)

        return _PLACEHOLDER.sub(substitute, template)

    def _build_uri(self, template: str, metadata: Dict[str, str], create: bool = True) -> str:
        target = Path(self.config.base_uri) / self._render_path(template, metadata)
        if create:
            target.parent.mkdir(parents=True, exist_ok=True)
        return str(target.resolve())


    @staticmethod
    def _bytes_written(uri: str, estimated: int) -> int:
        """The file's size; ``estimated`` when it is not there to measure."""
        path = Path(uri)
        return path.stat().st_size if path.exists() else estimated


def build_artifact_store() -> ArtifactStore:
    """The results store the settings name. The engine sets ``result_artifact_base_uri`` to
    ``<storage.path>/results/`` (the copy step, until the settings global is deleted)."""
    return ArtifactStore(
        ArtifactStoreConfig(
            base_uri=settings.result_artifact_base_uri,
            path_template=settings.result_artifact_path_template,
        )
    )
