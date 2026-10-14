"""``engine.datasources``: the engine's datasources, listed, added, removed, tested and their schema."""

from __future__ import annotations

from typing import Any, Dict, List

from nl2sql.api.types import DatasourceInfo
from nl2sql.check import DatasourceStatus
from nl2sql.config import Datasource
from nl2sql.datasources.registry import mask_connection_args
from nl2sql.runtime import Runtime


class DatasourcesAPI:
    """``engine.datasources``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def list(self) -> List[DatasourceInfo]:
        """Every configured datasource, in config order, failed ones included (``status == "failed"``)."""
        return [self._info(datasource) for datasource in self._ctx.config.datasources]

    def get(self, datasource_id: str) -> DatasourceInfo:
        """One configured datasource, its connection masked.

        Raises:
            ValueError: it is not configured.
        """
        for datasource in self._ctx.config.datasources:
            if datasource.id == datasource_id:
                return self._info(datasource)
        raise ValueError(f"Unknown datasource ID: {datasource_id}")

    def add(self, datasource: Datasource) -> None:
        """Add ``datasource``: its adapter is built, not connected, and it joins ``engine.config``.

        Index it next (``engine.index.build([datasource.id])``) so questions can reach it.

        Raises:
            ValueError: its id is taken, or its adapter cannot be built.
        """
        self._ctx.add_datasource(datasource)

    def remove(self, datasource_id: str) -> None:
        """Take a datasource out of routing and out of ``engine.config``, and close its pool.

        Its index entries stay until the next full ``index.build`` or ``index.clear``.

        Raises:
            ValueError: it is not configured.
        """
        self._ctx.remove_datasource(datasource_id)

    def schema(self, datasource_id: str) -> Dict[str, Any]:
        """The datasource's indexed schema, exactly as the planner is given it.

        Tables sorted by name, each with its columns (type, nullable, primary
        key, description), foreign keys, row count and description. Read from
        the snapshot the datasource's live index build was made from, not the
        database; before the first index ``tables`` is empty.
        """
        from nl2sql.schema.view import schema_view

        return schema_view(datasource_id, self._ctx.vector_store.snapshot(datasource_id))

    def test(self, datasource_id: str) -> DatasourceStatus:
        """Connect to one datasource, as ``check()`` does: ``ready`` puts it back in routing.

        Raises:
            ValueError: it is not configured.
        """
        return self._ctx.check_datasource(datasource_id)

    def _info(self, datasource: Datasource) -> DatasourceInfo:
        registry = self._ctx.ds_registry
        reason = registry.unavailable().get(datasource.id)
        if reason is not None:
            status, capabilities = "failed", []
        else:
            status = "ready" if registry.is_ready(datasource.id) else "unchecked"
            capabilities = sorted(registry.get_capabilities(datasource.id))
        adapter = datasource.adapter
        kind = datasource.type or getattr(adapter, "datasource_engine_type", None) or type(adapter).__name__
        return DatasourceInfo(id=datasource.id, type=kind, description=datasource.description, status=status,
                              capabilities=capabilities, connection=mask_connection_args(datasource.connection),
                              reason=reason)
