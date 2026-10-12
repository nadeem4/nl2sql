"""
Curated answers API for NL2SQL.

Validates ``curated.yaml``: the checks startup runs, plus the one that needs
the index (every table a verified query reads exists in its datasource's
latest indexed schema).
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from nl2sql.common.settings import settings
from nl2sql.configs import ConfigManager
from nl2sql.configs.curated import CuratedFileConfig, check_curated_queries
from nl2sql.context import NL2SQLContext
from nl2sql.datasources import DatasourceRegistry
from nl2sql.schema import build_schema_store
from nl2sql.secrets import secret_manager


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


class CuratedAPI:
    """
    API for checking the curated answers file.
    """

    def __init__(self, ctx: Optional[NL2SQLContext] = None):
        self._ctx = ctx

    def validate(
        self,
        curated_path: Optional[pathlib.Path] = None,
        datasources_path: Optional[pathlib.Path] = None,
        secrets_path: Optional[pathlib.Path] = None,
    ) -> CuratedValidationReport:
        """
        Validate ``curated.yaml`` against the configured datasources and, where
        a datasource has been indexed, its latest indexed schema.

        Runs whether or not ``CURATED_ENABLED`` is on. ``ok`` is False on any
        error; ``notes`` say what could not be checked.
        """
        cm = self._ctx.config_manager if self._ctx else ConfigManager()
        path = pathlib.Path(curated_path or settings.curated_config_path)

        try:
            cfg = cm.load_curated(path)
        except Exception as e:
            return CuratedValidationReport(ok=False, path=str(path), errors=[str(e)])

        try:
            registry = self._registry(cm, datasources_path, secrets_path)
            dialects = {ds_id: registry.get_dialect(ds_id) for ds_id in registry.list_ids()}
        except Exception as e:
            return CuratedValidationReport(ok=False, path=str(path), errors=[str(e)])

        report = CuratedValidationReport(ok=True, path=str(path))
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

    def _registry(self, cm: ConfigManager, datasources_path, secrets_path) -> DatasourceRegistry:
        if self._ctx and datasources_path is None:
            return self._ctx.ds_registry
        secret_configs = cm.load_secrets(pathlib.Path(secrets_path or settings.secrets_config_path))
        if secret_configs:
            secret_manager.configure(secret_configs)
        registry = DatasourceRegistry(secret_manager)
        registry.register_datasources(cm.load_datasources(pathlib.Path(datasources_path or settings.datasource_config_path)))
        return registry

    def _indexed_tables(
        self, cfg: CuratedFileConfig, dialects: Dict[str, str], report: CuratedValidationReport
    ) -> Dict[str, Set[Tuple[str, str]]]:
        """``(schema, table)`` names, lower-cased, per indexed datasource in ``cfg``.

        Never creates a schema store: with no index, the table check is skipped
        and a note says so.
        """
        wanted = [ds_id for ds_id in cfg.datasources if ds_id in dialects]
        if not wanted:
            return {}
        store = self._ctx.schema_store if self._ctx else None
        owned = False
        if store is None:
            store_path = pathlib.Path(settings.schema_store_path)
            if not store_path.exists():
                report.notes.append(f"No schema store at {store_path}: datasources not indexed, table check skipped.")
                return {}
            store = build_schema_store(settings.schema_store_backend, settings.schema_store_max_versions, path=store_path)
            owned = True
        try:
            out: Dict[str, Set[Tuple[str, str]]] = {}
            for ds_id in wanted:
                snapshot = store.get_latest_snapshot(ds_id)
                if snapshot is None:
                    report.notes.append(f"'{ds_id}' is not indexed: table check skipped. Run `nl2sql index`.")
                    continue
                out[ds_id] = {
                    (t.table.schema_name.lower(), t.table.table_name.lower())
                    for t in snapshot.contract.tables.values()
                }
            return out
        finally:
            if owned and hasattr(store, "close"):
                store.close()
