"""The trace and result stores, retention and ``engine.storage``.

docs/architecture/phase2.md, section 4 ("The interfaces", "Migrations and
retention", "The facade") and the S4 row of section 12: the engine writes and
reads traces through the ``TraceStore`` and results through the
``ResultStore``, so any store in those slots is used; ``files`` and ``local``
take ``max_age_days``; ``engine.storage.purge()`` applies it.
"""
from __future__ import annotations

import io
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar, Dict, Iterable

import polars as pl
import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from pydantic import BaseModel, ConfigDict

from nl2sql import NL2SQL, ConfigError, Embeddings, Storage, StoreConfig, UserContext
from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.execution.artifacts.results import ResultManifest, ResultOwner, ResultSummary
from nl2sql.runtime import Runtime
from nl2sql.storage.files import FilesTraces
from nl2sql.storage.local import LocalResults
from nl2sql.testing.config import make_config

TRACE_ID = "0b8f7d2e-1111-4222-8333-944455556666"


class _NoKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryTraces:
    """A plugin trace store that keeps documents in a dict."""

    interface_version: ClassVar[int] = 5
    slot: ClassVar[str] = "traces"
    config_model: ClassVar[type] = _NoKeys
    max_age_days = 3

    def __init__(self) -> None:
        self.documents: Dict[str, dict] = {}
        self.purged_before = None

    @classmethod
    def from_config(cls, cfg, *, folder: Path) -> "MemoryTraces":
        return cls()

    def write(self, trace_id: str, document: dict) -> None:
        self.documents[trace_id] = document

    def get(self, trace_id: str):
        return self.documents.get(trace_id)

    def purge(self, older_than: datetime) -> int:
        self.purged_before = older_than
        return 0


class MemoryResults:
    """A plugin results store that keeps bytes in a dict, as a remote store would."""

    interface_version: ClassVar[int] = 5
    slot: ClassVar[str] = "results"
    config_model: ClassVar[type] = _NoKeys

    def __init__(self) -> None:
        self.blobs: Dict[str, bytes] = {}

    @classmethod
    def from_config(cls, cfg, *, folder: Path) -> "MemoryResults":
        return cls()

    def write(self, key: str, chunks: Iterable[bytes]) -> int:
        self.blobs[key] = b"".join(chunks)
        return len(self.blobs[key])

    def open(self, key: str):
        return io.BytesIO(self.blobs[key])

    def exists(self, key: str) -> bool:
        return key in self.blobs

    def purge(self, older_than: datetime) -> int:
        return 0


def _config(tmp_path: Path, **storage):
    return make_config(tmp_path).model_copy(update={
        "storage": Storage(path=tmp_path / ".nl2sql", **storage),
        "embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))})


def _age(path: Path, days: float) -> None:
    then = time.time() - days * 86400
    os.utime(path, (then, then))


def _doc(trace_id: str = TRACE_ID) -> dict:
    return {"trace_format_version": 1, "trace_id": trace_id, "nodes": []}


# ---------------------------------------------------------------- the built-ins


def test_purge_deletes_only_older_files(tmp_path):
    traces = FilesTraces(tmp_path / "traces")
    traces.write("old-run", _doc("old-run"))
    traces.write("new-run", _doc("new-run"))
    [old] = (tmp_path / "traces").glob("*_old-run.json")
    _age(old, 20)

    results = LocalResults(tmp_path / "results")
    results.write("t1/old/manifest.json", [b"{}"])
    results.write("t1/old/result_a.parquet", [b"PAR1", b"PAR1"])
    results.write("t1/new/manifest.json", [b"{}"])
    for path in (tmp_path / "results" / "t1" / "old").iterdir():
        _age(path, 20)

    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    assert traces.purge(cutoff) == 1
    assert results.purge(cutoff) == 2

    assert traces.get("old-run") is None and traces.get("new-run") == _doc("new-run")
    assert not results.exists("t1/old/manifest.json") and results.exists("t1/new/manifest.json")
    assert not (tmp_path / "results" / "t1" / "old").exists(), "an emptied run folder is removed"


def test_local_results_round_trip_and_refuse_a_key_outside_the_folder(tmp_path):
    results = LocalResults(tmp_path / "results")

    assert results.write("t1/r1/sq.parquet", [b"ab", b"cd"]) == 4
    assert results.exists("t1/r1/sq.parquet") and not results.exists("t1/r1/other.parquet")
    with results.open("t1/r1/sq.parquet") as stream:
        assert stream.read() == b"abcd"
    for key in ("../escape.parquet", "t1/../../escape.parquet", str((tmp_path / "abs.parquet").resolve())):
        with pytest.raises(ValueError):
            results.write(key, [b"x"])


def test_files_traces_get_returns_the_newest_document_and_checks_the_id(tmp_path):
    traces = FilesTraces(tmp_path / "traces")
    assert traces.get(TRACE_ID) is None

    traces.write(TRACE_ID, _doc())
    assert traces.get(TRACE_ID) == _doc()
    with pytest.raises(ValueError):
        traces.get("../secrets")


def test_files_and_local_take_max_age_days(tmp_path):
    runtime = Runtime.components(_config(tmp_path))
    assert (runtime.traces.max_age_days, runtime.results.max_age_days) == (14, 7)

    runtime = Runtime.components(_config(tmp_path, traces=StoreConfig(type="files", max_age_days=30),
                                         results=StoreConfig(type="local", max_age_days=1)))
    assert (runtime.traces.max_age_days, runtime.results.max_age_days) == (30, 1)

    with pytest.raises(ConfigError) as error:
        Runtime.components(_config(tmp_path, traces=StoreConfig(type="files", max_age_days=0)))
    [problem] = error.value.problems
    assert problem.path == "storage.traces" and "max_age_days" in problem.message


# ---------------------------------------------------------------- through the interfaces


def test_trace_get_reads_through_the_store(tmp_path):
    store = MemoryTraces()
    store.write(TRACE_ID, _doc())

    with NL2SQL(_config(tmp_path, traces=store)) as engine:
        assert engine.traces.get(TRACE_ID) == _doc()
        assert engine.traces.get("other-run") is None
        with pytest.raises(ValueError):
            engine.traces.get("../escape")
    assert not (tmp_path / ".nl2sql" / "traces").exists()


def test_result_pages_read_through_the_store(tmp_path):
    store = MemoryResults()

    with NL2SQL(_config(tmp_path, results=store)) as engine:
        artifacts = engine._runtime.artifact_store
        tenant = engine.config.settings.tenant_id
        frame = pl.LazyFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
        artifacts.write_lazy(frame, ArtifactStore.run_metadata(tenant, "run-1", "result_n1"))
        artifacts.write_manifest(tenant, "run-1", ResultManifest(
            request_id="run-1", owner=ResultOwner(tenant_id=tenant, user_id="ann"),
            results=[ResultSummary(node_id="n1", columns=["id", "name"], row_count=3)]))
        ann = UserContext(user_id="ann")

        page = engine.results.page("run-1", "n1", offset=1, limit=5, user_context=ann)
        csv = b"".join(engine.results.csv("run-1", "n1", user_context=ann)).decode()

    assert page.rows == [[2, "b"], [3, "c"]] and page.total_rows == 3
    assert csv.splitlines() == ["id,name", "1,a", "2,b", "3,c"]
    assert sorted(store.blobs) == [f"{tenant}/run-1/manifest.json", f"{tenant}/run-1/result_n1.parquet"]
    assert not (tmp_path / ".nl2sql" / "results").exists()


def test_artifact_ref_uri_is_the_key_in_the_results_store(tmp_path):
    store = MemoryResults()
    artifacts = ArtifactStore(store)
    from nl2sql_sdk.contracts import ResultFrame

    frame = ResultFrame.from_row_dicts([{"x": 1}, {"x": 2}], columns=["x"], row_count=2, success=True)
    ref = artifacts.write_batches([frame], {"tenant_id": "t1", "request_id": "r1", "sub_query_id": "sq1"})

    assert ref.uri == "t1/r1/sq1.parquet" and store.exists(ref.uri)
    assert ref.bytes == len(store.blobs[ref.uri])
    assert artifacts.read_parquet(ref)["x"].to_list() == [1, 2]


# ---------------------------------------------------------------- engine.storage


def test_storage_purge_applies_each_stores_max_age(tmp_path):
    config = _config(tmp_path, traces=StoreConfig(type="files", max_age_days=10))
    with NL2SQL(config) as engine:
        traces = engine._runtime.traces
        traces.write("old-run", _doc("old-run"))
        traces.write("new-run", _doc("new-run"))
        [old] = traces.directory.glob("*_old-run.json")
        _age(old, 11)
        engine._runtime.results.write("t1/old/manifest.json", [b"{}"])
        _age(engine._runtime.results.directory / "t1" / "old" / "manifest.json", 8)

        assert engine.storage.purge() == {"traces": 1, "results": 1, "proposals": 0, "audit": 0, "conversations": 0, "dashboards": 0}
        assert engine.traces.get("new-run") is not None


def test_storage_purge_skips_a_store_without_max_age_days(tmp_path):
    traces = MemoryTraces()
    results = MemoryResults()
    with NL2SQL(_config(tmp_path, traces=traces, results=results)) as engine:
        before = datetime.now(timezone.utc)
        assert engine.storage.purge() == {"traces": 0, "proposals": 0, "audit": 0, "conversations": 0, "dashboards": 0}

    assert before - timedelta(days=3, seconds=5) < traces.purged_before < before - timedelta(days=3) + timedelta(
        seconds=5)


def test_storage_info_names_each_slot_and_the_index_namespace(tmp_path):
    with NL2SQL(_config(tmp_path, results=MemoryResults())) as engine:
        info = engine.storage.info()
        namespace = engine._runtime.namespace

    folder = tmp_path / ".nl2sql"
    assert info["namespace"] == namespace
    assert info["index"] == {"type": "chroma", "location": str(folder / "index")}
    assert info["state"] == {"type": "sqlite", "location": str(folder / "state.db")}
    assert info["traces"] == {"type": "files", "location": str(folder / "traces"), "max_age_days": 14}
    assert info["results"] == {"type": "MemoryResults", "location": None}
