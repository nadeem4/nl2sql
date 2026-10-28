"""``engine.datasources``: the engine's datasources, listed, added, removed, tested and their schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from nl2sql.api.types import Candidate, DatasourceInfo, DatasourceType
from nl2sql.check import DatasourceStatus
from nl2sql.common.planned import arrives_with
from nl2sql.config import Datasource
from nl2sql.datasources.registry import mask_connection_args
from nl2sql.policies import UserContext
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

    def schema(self, datasource_id: str, *, user_context: UserContext) -> Dict[str, Any]:
        """The datasource's indexed schema as ``user_context`` may see it, exactly as the planner is given it.

        Tables sorted by name, each with its columns (type, nullable, primary
        key, description), foreign keys, row count and description. Read from
        the snapshot the datasource's live index build was made from, not the
        database; before the first index ``tables`` is empty.

        A table the caller's scope denies is left out, and so is a foreign key
        that refers to one; a caller whose roles do not reach the datasource
        sees no tables. ``user_context`` is required: there is no default
        view, so a caller that forgot it fails rather than seeing everything.
        Pass an admin's ``UserContext`` for the whole schema.
        """
        from nl2sql.schema.view import schema_view

        scope = self._ctx.policy_engine.scope(user_context, datasource_id)
        return schema_view(datasource_id, self._ctx.vector_store.snapshot(datasource_id), allows=scope.allows)

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

    def scan(self, folder: Union[str, Path] = ".", *, url: Optional[str] = None, ports: bool = True) -> List[Candidate]:
        """The databases found under ``folder``, at ``url`` and on loopback ports, as connection candidates.

        Opens no database session (L1 fills this; docs/architecture/wave_c.md, section 7).
        """
        raise arrives_with("engine.datasources.scan", "L1")

    def diagram(self, datasource_id: str, *, user_context: UserContext, tables: Optional[List[str]] = None) -> str:
        """The datasource's tables and relationships the caller's scope reaches, as Mermaid text (K2 fills this;
        docs/architecture/wave_c.md, section 4)."""
        raise arrives_with("engine.datasources.diagram", "K2")

    def save(self, datasource_id: str, *, by: UserContext, env_file: bool = False) -> Path:
        """One datasource as ``datasources/<id>.yaml`` beside the config the engine was loaded from; ``by`` must
        hold an admin role (L2 fills this; docs/architecture/wave_c.md, section 7)."""
        raise arrives_with("engine.datasources.save", "L2")
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
