"""
Curated answers API for NL2SQL.

Validates the engine's ``curated`` section: the checks startup runs, plus the
one that needs the index (every table a verified query reads exists in its
datasource's latest indexed schema). Tests it: every verified query run once
against its database, with its defaults and examples bound.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from nl2sql_sdk.capabilities import DatasourceCapability
from nl2sql_sdk.contracts import AdapterRequest

from nl2sql.config import Curated
from nl2sql.config.curated import VerifiedQuery, check_curated_queries
from nl2sql.runtime import Runtime
from nl2sql.datasources import DatasourceRegistry
from nl2sql.execution.executor.sql_executor import _frames as adapter_frames

NO_CURATED = "The config has no curated section, so there are no verified queries to check."


@dataclass
class CuratedValidationEntry:
    datasource_id: str
    query_id: str
    status: str  # "OK" or "ERROR"
    details: str


@dataclass
class CuratedValidationReport:
    ok: bool
    path: str = ""
    entries: List[CuratedValidationEntry] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class CuratedTestEntry:
    datasource_id: str
    query_id: str
    status: str  # "PASS" or "FAIL"
    details: str
    params: Dict[str, Any] = field(default_factory=dict)
    row_count: Optional[int] = None


@dataclass
class CuratedTestReport:
    ok: bool
    path: str = ""
    entries: List[CuratedTestEntry] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class CuratedAPI:
    """
    API for checking the engine's curated answers.
    """

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def _source(self) -> str:
        folder = self._ctx.config.folder
        return (folder / "nl2sql.yaml").as_posix() if folder is not None else "Config"

    def validate(self) -> CuratedValidationReport:
        """
        Validate the ``curated`` section against the engine's datasources and,
        where a datasource has been indexed, its latest indexed schema.

        ``ok`` is False on any error; ``notes`` say what could not be checked.
        """
        cfg = self._ctx.config.curated
        path = self._source()
        if cfg is None:
            return CuratedValidationReport(ok=False, path=path, errors=[NO_CURATED])
        registry = self._ctx.ds_registry
        dialects = {ds_id: registry.get_dialect(ds_id) for ds_id in registry.list_ids()}

        report = CuratedValidationReport(ok=True, path=path)
        check = check_curated_queries(cfg, dialects)
        for issue in check.issues:
            if issue.query_id is None:
                report.errors.append(str(issue))
            else:
                report.entries.append(CuratedValidationEntry(issue.datasource_id, issue.query_id, "ERROR", issue.message))

        indexed = self._indexed_tables(cfg, dialects, report)
        for (ds_id, query_id), parsed in check.parsed.items():
            tables = indexed.get(ds_id)
            if tables is None:
                report.entries.append(CuratedValidationEntry(ds_id, query_id, "OK", "Static checks passed"))
                continue
            missing = [
                f"{db}.{name}" if db else name
                for db, name in parsed.tables
                if not ((db.lower(), name.lower()) in tables or (not db and name.lower() in {t for _, t in tables}))
            ]
            if missing:
                report.entries.append(CuratedValidationEntry(
                    ds_id, query_id, "ERROR", f"Tables not in the indexed schema: {', '.join(missing)}"))
            else:
                report.entries.append(CuratedValidationEntry(ds_id, query_id, "OK", "Verified against the indexed schema"))

        report.ok = not report.errors and all(e.status == "OK" for e in report.entries)
        return report

    def test(self) -> CuratedTestReport:
        """
        Run every verified query once against its database.

        Each param is bound to its ``default``, else its ``example``; a param
        with neither fails the query without running it. A query that fails
        the static checks ``validate()`` runs (read-only, placeholders) is
        never run. The rest run read-only through their datasource's adapter,
        with the values bound, and only the first batch of rows is fetched.
        ``ok`` is False if any query fails.
        """
        cfg = self._ctx.config.curated
        path = self._source()
        if cfg is None:
            return CuratedTestReport(ok=False, path=path, errors=[NO_CURATED])
        registry = self._ctx.ds_registry
        dialects = {ds_id: registry.get_dialect(ds_id) for ds_id in registry.list_ids()}

        report = CuratedTestReport(ok=True, path=path)
        check = check_curated_queries(cfg, dialects)
        for issue in check.issues:
            if issue.query_id is None:
                report.errors.append(str(issue))
            else:
                report.entries.append(CuratedTestEntry(issue.datasource_id, issue.query_id, "FAIL", issue.message))

        for ds_id, ds in cfg.datasources.items():
            for query in ds.verified_queries:
                if (ds_id, query.id) in check.parsed:
                    report.entries.append(self._run_one(registry, ds_id, query))

        report.ok = not report.errors and all(e.status == "PASS" for e in report.entries)
        return report

    def _run_one(self, registry: DatasourceRegistry, ds_id: str, query: VerifiedQuery) -> CuratedTestEntry:
        params: Dict[str, Any] = {}
        for name, spec in query.params.items():
            value = spec.default if spec.default is not None else spec.example
            if value is None:
                return CuratedTestEntry(ds_id, query.id, "FAIL",
                                        f"param '{name}' has no default or example to test with")
            params[name] = value
        if params and not registry.supports(ds_id, DatasourceCapability.SUPPORTS_BOUND_PARAMETERS):
            return CuratedTestEntry(ds_id, query.id, "FAIL",
                                    f"the adapter for '{ds_id}' cannot bind parameters", params)

        request = AdapterRequest(plan_type="sql", payload={"sql": query.sql}, parameters=params, limits={})
        started = time.perf_counter()
        frames = adapter_frames(registry.get_adapter(ds_id), request, self._ctx.settings.results.fetch_batch_size)
        try:
            frame = next(frames, None)
        except Exception as e:  # a driver error the adapter did not wrap
            return CuratedTestEntry(ds_id, query.id, "FAIL", str(e), params)
        finally:
            close = getattr(frames, "close", None)
            if close is not None:
                close()
        if frame is None or not frame.success:
            reason = frame.error.safe_message if frame is not None and frame.error else "no result"
            return CuratedTestEntry(ds_id, query.id, "FAIL", reason, params)
        elapsed = time.perf_counter() - started
        rows = f"{frame.row_count} row{'' if frame.row_count == 1 else 's'}"
        return CuratedTestEntry(ds_id, query.id, "PASS", f"{rows} in {elapsed:.2f} s", params, frame.row_count)

    def _indexed_tables(
        self, cfg: Curated, dialects: Dict[str, str], report: CuratedValidationReport
    ) -> Dict[str, Set[Tuple[str, str]]]:
        """``(schema, table)`` names, lower-cased, per indexed datasource in ``cfg``.

        With no snapshot for a datasource, its table check is skipped and a
        note says so.
        """
        out: Dict[str, Set[Tuple[str, str]]] = {}
        for ds_id in (ds_id for ds_id in cfg.datasources if ds_id in dialects):
            snapshot = self._ctx.schema_store.get_latest_snapshot(ds_id)
            if snapshot is None:
                report.notes.append(f"'{ds_id}' is not indexed: table check skipped. Run `nl2sql index`.")
                continue
            out[ds_id] = {
                (t.table.schema_name.lower(), t.table.table_name.lower())
                for t in snapshot.contract.tables.values()
            }
        return out
