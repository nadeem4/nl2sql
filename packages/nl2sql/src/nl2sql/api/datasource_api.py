"""``engine.datasources``: the engine's datasources, listed, added, removed, tested and their schema."""

from __future__ import annotations

from typing import Any, Dict, List, Union

from nl2sql.api.types import DatasourceInfo, DatasourceType
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

        ``connection`` is validated against the adapter's connection model
        and ``index`` as an ``IndexScope``. Index it next
        (``engine.index.build([datasource.id])``) so questions can reach it.

        Raises:
            ValueError: its id is taken, its ``index`` or ``connection`` does
                not validate (no value is shown), or its adapter cannot be built.
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

    def types(self) -> List[DatasourceType]:
        """Every adapter the engine's registry can name, installed or not, with its connection form.

        Sorted by name. An adapter module imports without its driver, so an
        uninstalled one is listed with ``installed=False``, its install hint
        and its schema. A plugin that fails to import or implements another
        interface version is left out.
        """
        from nl2sql.registry import missing_modules

        registry = self._ctx.registry
        found = []
        for name in registry.names("adapter"):
            try:
                adapter_cls = registry.load("adapter", name, require_installed=False)
            except Exception:  # a plugin that fails to import or its version check
                continue
            found.append(DatasourceType(
                name=name, title=getattr(adapter_cls, "title", None) or name,
                installed=not missing_modules("adapter", adapter_cls),
                install_hint=getattr(adapter_cls, "install_hint", ""),
                connection_schema=adapter_cls.connection_model.model_json_schema()))
        return found

    def test(self, datasource: Union[Datasource, str]) -> DatasourceStatus:
        """Connect to a datasource and report the stage that failed, if one did.

        With an id, connects to that configured datasource as ``check()``
        does: ``ready`` puts it back in routing, ``failed`` keeps it out. With
        a ``Datasource``, builds its adapter, runs ``test_connection`` and
        closes it, and registers nothing: what a Connect a database form
        calls before ``add``. Its failures are reported, not raised:
        ``config`` (unknown type or connection), ``driver`` (with the install
        hint as the fix), or the adapter's own stage.

        Raises:
            ValueError: an id that is not configured.
        """
        if isinstance(datasource, str):
            return self._ctx.check_datasource(datasource)
        return self._ctx.try_datasource(datasource)

    def _info(self, datasource: Datasource) -> DatasourceInfo:
        registry = self._ctx.ds_registry
        reason = registry.unavailable().get(datasource.id)
        if reason is not None:
            status, capabilities = "failed", []
        else:
            status = "ready" if registry.is_ready(datasource.id) else "unchecked"
            capabilities = sorted(registry.get_capabilities(datasource.id))
        adapter = datasource.adapter
        kind = datasource.type or getattr(adapter, "title", None) or type(adapter).__name__
        return DatasourceInfo(id=datasource.id, type=kind, description=datasource.description, status=status,
                              capabilities=capabilities, connection=mask_connection_args(datasource.connection),
                              reason=reason)
