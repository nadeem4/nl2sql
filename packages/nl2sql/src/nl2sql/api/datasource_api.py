"""``engine.datasources``: the engine's datasources, listed, added, removed, tested and their schema."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, SecretStr

from nl2sql.api.types import Candidate, DatasourceInfo, DatasourceType
from nl2sql.check import DatasourceStatus
from nl2sql.common.planned import arrives_with
from nl2sql.config import Datasource
from nl2sql.config.models import is_secret_key
from nl2sql.datasources.registry import mask_connection_args
from nl2sql.policies import UserContext
from nl2sql.runtime import Runtime


# One save at a time in this process, whichever engine: two saves share one ``.env`` and one ``datasources/`` folder.
_SAVING = threading.Lock()


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
        return self._scrubbed(self._ctx.try_datasource(datasource), datasource)

    def scan(self, folder: Union[str, Path] = ".", *, url: Optional[str] = None, ports: bool = True) -> List[Candidate]:
        """The databases found under ``folder``, at ``url`` and on loopback ports, as connection candidates.

        Looks in three places and lists what it finds in this order, each once: ``url`` (its scheme, up to any
        ``+driver``, picks the adapter; pass ``DATABASE_URL`` here), files under ``folder`` whose suffix an adapter
        declares (two folders down, at most 50, no hidden folder and not ``storage.path``), and with ``ports`` the
        default ports of the adapters on ``127.0.0.1`` only (0.2 s each). Every adapter the registry names takes part,
        installed or not, and only the adapters that declare ``DiscoverableAdapter`` are found.

        Each ``Candidate`` carries the connection fields the finding fills in and the required ones still
        ``missing``. Nothing connects: ``scan`` opens no database session and builds no adapter; ``test`` does.
        A URL's password is in ``connection`` (masked when the candidate is printed) and never in ``found``.
        """
        from nl2sql.datasources.discovery import scan

        return scan(self._ctx.registry, Path(folder), url=url, ports=ports, skip=self._ctx.config.storage.path)

    def dialect(self, datasource_id: str) -> str:
        """The sqlglot dialect name of the datasource's adapter (``postgres``, ``tsql``...); raises for a datasource the engine does not have."""
        return self._ctx.ds_registry.get_dialect(datasource_id)

    def diagram(self, datasource_id: str, *, user_context: UserContext, tables: Optional[List[str]] = None) -> str:
        """The datasource's tables and relationships ``user_context`` may see, as a Mermaid ``erDiagram``.

        Drawn from ``schema(...)``, so exactly the tables the planner would be given for that caller: a denied table,
        and every foreign key or glossary join that names one, is left out. Each table shows its primary and foreign
        key columns; each foreign key and each glossary join the caller may see is a relationship. With ``tables``,
        those tables and the ones one relationship away; without, the whole datasource, cut to the 30 with the most
        relationships and a ``%% N more tables not shown`` comment says so. No colour, no ``%%{init}`` line, no
        model call. ``user_context`` is required, as for ``schema``.

        Raises:
            ValueError: ``tables`` names no table the caller may see.
        """
        from nl2sql.charts.diagram import schema_diagram
        from nl2sql.knowledge.scope import deny_names, visible

        view = self.schema(datasource_id, user_context=user_context)
        joins: List[Any] = []
        scope = self._ctx.policy_engine.scope(user_context, datasource_id)
        snapshot = self._ctx.vector_store.snapshot(datasource_id) if view["tables"] else None
        if snapshot is not None:
            joins = visible(self._ctx.knowledge.get(datasource_id), snapshot, scope.allows, deny_names(scope)).joins
        return schema_diagram(view, tables, joins=joins)


    def save(self, datasource_id: str, *, by: UserContext, env_file: bool = False) -> Path:
        """One datasource as ``datasources/<id>.yaml`` beside the config the engine was loaded from; ``by`` must
        hold an admin role. Returns the file.

        Every secret field is written as a reference (D122): a literal in the connection becomes
        ``${env:NL2SQL_<ID>_<KEY>}`` and, with ``env_file``, its value goes to the ``.env`` beside ``nl2sql.yaml``
        (the other lines are kept); without it a literal is refused. A reference is written as it is. The
        engine keeps running on the value it holds. Like an accept, it records each datasource file's SHA-256
        when the config is loaded and refuses to replace a file that has changed since (a new file must not
        exist yet). The file is written to a temporary file and renamed. Publishes ``datasource.saved``
        (audited). Never connects and never calls a model.

        Raises:
            TypeError: ``by`` is not a ``UserContext``.
            PermissionError: ``by`` holds none of ``policies.admin_roles``.
            ValueError: the datasource is not configured; the engine was built in code or its ``datasources``
                section is not a ``datasources/`` folder; the id cannot be a file name; or two secrets map to
                one environment variable.
            SecretValueInConfig: a secret is a literal and ``env_file`` is false.
            ReviewError: ``FILE_CHANGED``, the file changed on disk since the config was loaded (reload the
                engine). Nothing is written.
        """
        from nl2sql.config.writer import (EnvSecrets, adapter_secret_fields, check_file_name, config_reserved_names,
                                          datasource_data, dump_yaml, file_sha256, reserved_names, write_atomic,
                                          write_env_values)
        from nl2sql.review.models import ReviewError, ReviewErrorCode

        by = self._admin(by)
        config = self._ctx.config
        datasource = next((d for d in config.datasources if d.id == datasource_id), None)
        if datasource is None:
            raise ValueError(f"Unknown datasource ID: {datasource_id}")
        folder, project = config.datasources_folder, config.folder
        if folder is None or project is None:
            raise ValueError("Saving a datasource needs the engine to be loaded from a nl2sql.yaml whose datasources "
                             "section is a folder (`datasources: { file: datasources/ }`, one <id>.yaml each); this "
                             "engine was built in code or its datasources are inline.")
        name = check_file_name(datasource_id)
        secrets = EnvSecrets(env_file=env_file, reserved={
            **reserved_names(d for d in config.datasources if d.id != datasource_id), **config_reserved_names(config)})
        text = dump_yaml(datasource_data(
            datasource, secrets, adapter_secret_fields(self._ctx.registry, datasource.type)))
        target = folder / name
        data = text.encode("utf-8")
        with _SAVING:  # the check, the .env read-modify-write and the file write are one step
            if file_sha256(target) != config.datasource_file_hash(name):
                raise ReviewError(ReviewErrorCode.FILE_CHANGED,
                                  f"{target.name} changed on disk since the engine loaded it.",
                                  fix="Reload the engine (build it again from nl2sql.yaml) and save again.")
            if secrets.values:
                write_env_values(project, secrets.values)
            write_atomic(target, data)
            config.record_datasource_file(name, hashlib.sha256(data).hexdigest())
        self._ctx.bus.publish("datasource.saved", {
            "datasource_id": datasource_id, "file": f"{folder.name}/{name}", "by": _who(by)},
            tenant_id=self._ctx.tenant_id)
        return target

    def _scrubbed(self, status: DatasourceStatus, datasource: Datasource) -> DatasourceStatus:
        """``status`` with the datasource's secret literals, and every secret the engine knows, taken out of the
        driver's words: a driver may echo the password it was given, and a Connect form shows these."""
        from nl2sql.config.writer import adapter_secret_fields
        from nl2sql.tracing.trace import collect_secrets

        # Exact values only: a pattern would also eat the words around "password:" in a validation message.
        known = _secret_literals(datasource.connection, adapter_secret_fields(self._ctx.registry, datasource.type))
        known += [secret for secret in collect_secrets(self._ctx) if len(secret) >= 6]
        literals = sorted(set(known), key=len, reverse=True)

        def scrub(text: Optional[str]) -> Optional[str]:
            if not text:
                return text
            for literal in literals:
                text = text.replace(literal, "***")
            return text

        return replace(status, reason=scrub(status.reason), fix=scrub(status.fix))

    def _admin(self, by: Any) -> UserContext:
        if not isinstance(by, UserContext):
            raise TypeError("by must be a UserContext: saving a datasource needs a caller with an admin role "
                            "(policies.admin_roles).")
        policies = self._ctx.config.policies
        if not (policies.allow_all or set(by.roles) & set(policies.admin_roles)):
            raise PermissionError(f"Saving a datasource needs one of the admin roles "
                                  f"({', '.join(policies.admin_roles) or 'none defined'}).")
        return by

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


def _secret_literals(connection: Any, secret_fields: Any = ()) -> List[str]:
    """The secret values written out in ``connection`` (a reference is not one)."""
    if isinstance(connection, BaseModel):
        values = [v.get_secret_value() for v in connection.model_dump(mode="python").values() if isinstance(v, SecretStr)]
    else:
        values = [v for k, v in (connection or {}).items() if is_secret_key(k) or k in secret_fields]
    return [v for v in values if isinstance(v, str) and v and not v.startswith("${")]


def _who(by: UserContext) -> Optional[str]:
    """What an event records as the caller: the user id, else the roles."""
    return by.user_id or (",".join(by.roles) or None)
