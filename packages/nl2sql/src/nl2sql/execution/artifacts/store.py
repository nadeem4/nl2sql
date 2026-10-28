from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Iterator, List, Optional

import polars as pl

from nl2sql.execution.contracts import ArtifactRef
from nl2sql_sdk.contracts import ResultFrame
from nl2sql_sdk.stores import ResultStore

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


# A result's key in the results store.
DEFAULT_PATH_TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"

# Bytes read from a finished Parquet file per chunk handed to the results store.
_CHUNK_BYTES = 1 << 20


class ArtifactStore:
    """Writes a run's results as Parquet, and its manifest as JSON, through a ``ResultStore``; reads them back.

    The engine makes the bytes and the store keeps them under a key: the path
    template rendered for the result (``<tenant_id>/<request_id>/<sub_query_id>.parquet``
    by default), which is the ``uri`` of the ``ArtifactRef``. Each Parquet
    file is written to a temporary folder first and handed to the store in
    chunks.
    """

    def __init__(self, results: ResultStore, path_template: str = DEFAULT_PATH_TEMPLATE) -> None:
        self.results = results
        self.path_template = path_template

    def create_artifact_ref(self, frame: ResultFrame, metadata: Dict[str, str]) -> ArtifactRef:
        return self.write_batches([frame], metadata)

    def write_batches(self, frames: Iterable[ResultFrame], metadata: Dict[str, str]) -> ArtifactRef:
        """Writes a result that arrives a batch at a time to one parquet artifact.

        One batch is written as it always was. From the second on, each batch
        is written to its own part file in a temporary folder as it arrives,
        and the parts are then streamed into the artifact by polars
        (``vertical_relaxed``, so a column that is empty in one batch and text
        in the next is text). At most one batch is held in memory. If
        ``frames`` raises, nothing is stored and the parts are removed.
        """
        template = self._template_for(metadata)
        key = self._key(template, metadata)
        columns: Optional[List[str]] = None
        row_count = 0
        first: Optional[pl.DataFrame] = None
        spill_dir: Optional[str] = None
        parts: List[Path] = []
        work = tempfile.mkdtemp(prefix="nl2sql-result-")
        target = Path(work) / "result.parquet"
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
                sink_parquet(combined, target)
            else:
                write_parquet(first if first is not None else pl.DataFrame(), target)
            size = self.results.write(key, _chunks(target))
        finally:
            if spill_dir is not None:
                shutil.rmtree(spill_dir, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)

        return self._artifact_ref(key, template, columns or [], row_count, size, metadata)

    def write_lazy(self, frame: pl.LazyFrame, metadata: Dict[str, str]) -> ArtifactRef:
        """Writes a lazy result (the aggregator's combined result) to its own artifact.

        polars streams the plan into the file; the row count and columns are
        then read from the file's metadata, not by loading it.
        """
        template = self._template_for(metadata)
        key = self._key(template, metadata)
        work = tempfile.mkdtemp(prefix="nl2sql-result-")
        target = Path(work) / "result.parquet"
        try:
            sink_parquet(frame.lazy(), target)
            written = scan_parquet(target)
            row_count = int(written.select(pl.len()).collect().item())
            columns = written.collect_schema().names()
            size = self.results.write(key, _chunks(target))
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return self._artifact_ref(key, template, columns, row_count, size, metadata)

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
        """Writes a run's result manifest (a pydantic model) as JSON beside its results; returns its key."""
        key = self._manifest_key(tenant_id, request_id)
        self.results.write(key, [manifest.model_dump_json().encode("utf-8")])
        return key

    def withhold(self, tenant_id: str, request_id: str) -> None:
        """Makes a run's saved results unreadable: writes a marker beside its manifest.

        A hook ended the run, so its data is withheld from the caller; the files stay (a
        ``ResultStore`` has no delete, and a marker works the same on every store), but
        :meth:`read_manifest` and :meth:`scan_result` act as if the run had saved nothing.
        """
        self.results.write(self._withheld_key(tenant_id, request_id), [b"withheld"])

    def _withheld(self, tenant_id: str, request_id: str) -> bool:
        return self.results.exists(self._withheld_key(tenant_id, request_id))

    def _withheld_key(self, tenant_id: str, request_id: str) -> str:
        return self._manifest_key(tenant_id, request_id)[: -len(".json")] + ".withheld"

    def read_manifest(self, tenant_id: str, request_id: str) -> Optional[Dict[str, Any]]:
        """A run's result manifest as a dict, or None when there is none, or the run was withheld."""
        key = self._manifest_key(tenant_id, request_id)
        if self._withheld(tenant_id, request_id) or not self.results.exists(key):
            return None
        with self.results.open(key) as stream:
            return json.loads(stream.read().decode("utf-8"))

    def scan_result(self, tenant_id: str, request_id: str, node_id: str) -> Optional[pl.LazyFrame]:
        """A run's saved result as a polars scan, or None when the store has no such file or the run was withheld.

        The key is rendered from the template, exactly as the aggregator wrote it.
        """
        metadata = self.run_metadata(tenant_id, request_id, f"result_{node_id}")
        key = self._key(self._template_for(metadata), metadata)
        if self._withheld(tenant_id, request_id) or not self.results.exists(key):
            return None
        return scan_parquet(self._source(key))

    def _manifest_key(self, tenant_id: str, request_id: str) -> str:
        """The result template rendered with ``manifest`` as the name, as a ``.json`` file.

        The default template gives ``<tenant_id>/<request_id>/manifest.json``.
        """
        metadata = self.run_metadata(tenant_id, request_id, "manifest")
        key = self._key(self._template_for(metadata), metadata)
        stem = key[: -len(".parquet")] if key.endswith(".parquet") else key
        return f"{stem}.json"

    def read_result_frame(self, artifact: ArtifactRef) -> ResultFrame:
        return polars_to_result_frame(self.read_parquet(artifact))

    def read_parquet(self, artifact: ArtifactRef) -> pl.DataFrame:
        return read_parquet(self._source(artifact.uri))

    def scan_parquet(self, artifact: ArtifactRef) -> pl.LazyFrame:
        """The artifact as a polars scan: nothing is read until it is collected."""
        return scan_parquet(self._source(artifact.uri))

    def _source(self, key: str) -> Any:
        """What polars reads ``key`` from.

        A store that opens a local file (the ``local`` store) gives its path,
        so polars scans the file in place and a page or CSV slice reads only
        what it needs. Any other stream is read into memory first, since a
        scan outlives the call that opened it.
        """
        stream = self.results.open(key)
        name = getattr(stream, "name", None)
        if isinstance(stream, io.BufferedReader) and isinstance(name, str) and os.path.isfile(name):
            stream.close()
            return name
        with stream:
            return io.BytesIO(stream.read())

    @staticmethod
    def _spill(df: pl.DataFrame, folder: str, index: int) -> Path:
        path = Path(folder) / f"part-{index:05d}.parquet"
        df.write_parquet(path)
        return path

    def _artifact_ref(
        self,
        key: str,
        template: str,
        columns: List[str],
        row_count: int,
        size: int,
        metadata: Dict[str, str],
    ) -> ArtifactRef:
        payload = {
            "columns": columns,
            "row_count": row_count,
            "path": key,
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        return ArtifactRef(
            uri=key,
            format="parquet",
            row_count=row_count,
            columns=columns,
            bytes=size,
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
        template = self.path_template
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

    def _key(self, template: str, metadata: Dict[str, str]) -> str:
        """The rendered template as a results-store key, with ``/`` between its parts."""
        return PurePosixPath(self._render_path(template, metadata).replace("\\", "/")).as_posix()


def _chunks(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(_CHUNK_BYTES)
            if not chunk:
                return
            yield chunk
