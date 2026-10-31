"""A run's saved results, read back by request id.

The aggregator saves each terminal result of a run as a parquet artifact and a
:class:`ResultManifest` beside them (see :meth:`ArtifactStore.write_manifest`).
:class:`ResultReader` reads them back for one tenant folder and one caller: the
manifest, a page of a result, or the whole result as CSV, a slice at a time.
Nothing is read whole.

The manifest records who asked the run (:class:`ResultOwner`), and every read
goes through it: only that caller reads the run (a named user by tenant and user
id; an anonymous caller by tenant and the same set of roles). Ids are checked to be plain
tokens before any path is built from them, and the file read is always rendered
from the artifact template for the reader's own tenant, never taken from the
manifest. So an unknown request id, another caller's or another tenant's run, a
manifest that records no asker, and an id that is not a plain token are all the
same :class:`ResultNotFound`.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from pydantic import BaseModel, Field

from nl2sql.charts.spec import ColumnKind
from nl2sql.common.errors import NL2SQLError

# The most rows one page returns; a larger page size is cut to this.
MAX_RESULT_PAGE_SIZE = 1000
DEFAULT_RESULT_PAGE_SIZE = 100
# Rows read from the parquet file and written out per CSV chunk.
CSV_CHUNK_ROWS = 10_000

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def column_kinds(schema: Mapping[str, Any]) -> Dict[str, ColumnKind]:
    """Each column's kind (Wave A's D30 categories) from a polars schema, i.e. from the saved file's Arrow
    types: integer, decimal and float are ``number``; date, time and timestamp ``temporal``; string ``text``;
    bool ``boolean``; anything else ``other`` (docs/architecture/wave_c.md, D112)."""
    import polars as pl

    def kind(dtype: Any) -> ColumnKind:
        base = dtype.base_type()
        if base in (pl.Date, pl.Time, pl.Datetime):
            return "temporal"
        if base == pl.Boolean:
            return "boolean"
        if base in (pl.String, pl.Categorical, pl.Enum):
            return "text"
        return "number" if dtype.is_numeric() else "other"

    return {name: kind(dtype) for name, dtype in schema.items()}


class ResultSummary(BaseModel):
    """One saved result of a run: what it is, never its rows."""
    # The DAG node whose result this is; the ``result_id`` the paging and CSV routes take.
    node_id: str
    columns: List[str] = Field(default_factory=list)
    row_count: int = 0
    # True when a sub-query feeding it was cut at its row ceiling, so rows may be missing.
    truncated: bool = False
    # Each column's type category (Wave A's D30), from the saved file's schema, so a chart can be chosen
    # without reading rows. Empty until the artifact writer fills it (docs/architecture/wave_c.md, D112).
    column_kinds: Dict[str, ColumnKind] = Field(default_factory=dict)


class ResultOwner(BaseModel):
    """Who asked a run: the only caller its results are readable by."""
    # The caller's tenant, else the engine's.
    tenant_id: Optional[str] = None
    # The caller's user id; None for an anonymous caller (one the policies let in without a user id).
    user_id: Optional[str] = None
    # An anonymous caller's roles, sorted and unique, so only a caller with the same roles reads the run: else an
    # anonymous viewer holding an admin run's trace id would read rows its own policies hide. None for a named
    # user, whose run is bound to the user alone.
    roles: Optional[List[str]] = None

    @classmethod
    def of(cls, user: Any, default_tenant: Optional[str]) -> "ResultOwner":
        """The owner a caller (a ``UserContext``, or None for no caller at all) asks or reads as."""
        user_id = getattr(user, "user_id", None)
        return cls(tenant_id=getattr(user, "tenant_id", None) or default_tenant, user_id=user_id,
                   roles=None if user_id else sorted(set(getattr(user, "roles", None) or [])))


class ResultManifest(BaseModel):
    """Which results a run produced, and who asked it, written as ``manifest.json`` in the run's folder."""
    request_id: str
    owner: ResultOwner
    results: List[ResultSummary] = Field(default_factory=list)


class ResultPage(BaseModel):
    """``rows[i]`` is row ``offset + i`` of the result, in ``columns`` order."""
    request_id: str
    result_id: str
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    offset: int = 0
    # The page size used, after the cap.
    limit: int = DEFAULT_RESULT_PAGE_SIZE
    total_rows: int = 0
    # Where the next page starts, or None when this page reaches the end.
    next_offset: Optional[int] = None


class ResultNotFound(NL2SQLError, LookupError):
    """No such result for this caller: an unknown request id, another caller's or another tenant's, or an id that
    is not a plain token."""

    code, status = "NOT_FOUND", 404


def _plain(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


class ResultReader:
    """Reads, through an :class:`ArtifactStore`, the saved results in one tenant folder that ``owner`` asked for."""

    def __init__(self, store: Any, tenant_id: str, owner: ResultOwner) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._owner = owner.model_dump()

    def manifest(self, request_id: str) -> ResultManifest:
        if not _plain(request_id):
            raise ResultNotFound(f"No results for request '{request_id}'.")
        doc = self._store.read_manifest(self._tenant_id, request_id)
        # Another caller's run, and a manifest that records no asker, are the same "not found" as no run.
        if doc is None or doc.get("owner") != self._owner:
            raise ResultNotFound(f"No results for request '{request_id}'.")
        return ResultManifest.model_validate(doc)

    def page(self, request_id: str, result_id: str, offset: int = 0,
             limit: int = DEFAULT_RESULT_PAGE_SIZE) -> ResultPage:
        if offset < 0:
            raise ValueError("offset must be 0 or more.")
        if limit < 1:
            raise ValueError("limit must be 1 or more.")
        limit = min(limit, MAX_RESULT_PAGE_SIZE)
        summary, scan = self._open(request_id, result_id)
        frame = scan.slice(offset, limit).collect()
        end = offset + frame.height
        return ResultPage(
            request_id=request_id,
            result_id=result_id,
            columns=list(frame.columns) or list(summary.columns),
            rows=[list(row) for row in frame.rows()],
            offset=offset,
            limit=limit,
            total_rows=summary.row_count,
            next_offset=end if end < summary.row_count else None,
        )

    def head(self, request_id: str, result_id: str, limit: int) -> Tuple[ResultSummary, List[List[Any]]]:
        """The result's summary and its first ``limit`` rows (not capped at the page size: a chart's read)."""
        summary, scan = self._open(request_id, result_id)
        frame = scan.slice(0, limit).collect()
        return summary, [list(row) for row in frame.rows()]

    def csv(self, request_id: str, result_id: str) -> Iterator[bytes]:
        """The whole result as UTF-8 CSV, header first. Checks the ids before the stream starts."""
        summary, scan = self._open(request_id, result_id)
        return self._stream(scan, summary.row_count)

    @staticmethod
    def _stream(scan: Any, row_count: int) -> Iterator[bytes]:
        chunk = max(1, int(CSV_CHUNK_ROWS))
        offset = 0
        while True:
            frame = scan.slice(offset, chunk).collect()
            yield frame.write_csv(include_header=offset == 0).encode("utf-8")
            offset += chunk
            if offset >= row_count:
                return

    def _open(self, request_id: str, result_id: str):
        manifest = self.manifest(request_id)
        if not _plain(result_id):
            raise ResultNotFound(f"No result '{result_id}' for request '{request_id}'.")
        for summary in manifest.results:
            if summary.node_id == result_id:
                scan = self._store.scan_result(self._tenant_id, request_id, result_id)
                if scan is None:
                    break
                return summary, scan
        raise ResultNotFound(f"No result '{result_id}' for request '{request_id}'.")
