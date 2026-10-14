"""``check_config``: the whole config checked without starting the engine, for ``nl2sql config check``.

Steps 1, 2a and 2b of loading (docs/architecture/configuration_v2.md,
section 6): ``load_config`` reads every file, applies the includes and
resolves ``${env:...}``; ``Runtime.components`` loads every name the config
uses through the registry (interface version, installed drivers) and builds
the components without connecting. With ``connect``, step 3 too: each
datasource is connected to, as ``engine.check()`` does. No store is built, so
nothing is written under ``storage.path``.

Every secret reference is reported as present or missing; its value is never
kept. A missing LLM key is reported but is not a problem: as for the engine,
it is ``check()``'s to report at startup, and a CI job rarely holds the
production key. Any other secret that does not resolve is already a problem
from step 2b.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple

from pydantic import BaseModel

from nl2sql.config.errors import ConfigError, Problem
from nl2sql.config.loader import PathLike, config_path, load_config

if TYPE_CHECKING:
    from nl2sql.check import DatasourceStatus
    from nl2sql.config.models import Config
    from nl2sql.registry import Registry

_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_-]*):([^{}]+)\}")


@dataclass(frozen=True)
class SecretStatus:
    """One ``${provider:key}`` reference, where it is written, and whether it resolves. Never its value."""

    reference: str
    file: str
    path: str
    present: bool

    def __str__(self) -> str:
        where = f"{self.file} → {self.path}" if self.path else self.file
        return f"{self.reference} {'present' if self.present else 'missing'} ({where})"


@dataclass(frozen=True)
class ConfigCheck:
    """What ``check_config`` found. ``ok`` when there is no problem."""

    path: Path
    problems: List[Problem] = field(default_factory=list)
    secrets: List[SecretStatus] = field(default_factory=list)
    datasources: List["DatasourceStatus"] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def by_file(self) -> Dict[str, List[Problem]]:
        """The problems, grouped by the file each is in, files in the order first met."""
        grouped: Dict[str, List[Problem]] = {}
        for problem in self.problems:
            grouped.setdefault(problem.file, []).append(problem)
        return grouped


def check_config(path: Optional[PathLike] = None, *, registry: Optional["Registry"] = None,
                 connect: bool = False) -> ConfigCheck:
    """Check the config at ``path`` (default ``$NL2SQL_CONFIG``, then ``./nl2sql.yaml``).

    ``registry`` defaults to ``Registry.default()``. With ``connect``, every
    datasource is connected to and each one that fails is a problem. Never
    raises a ``ConfigError``: every problem is in the result.
    """
    from nl2sql.registry import Registry
    from nl2sql.runtime import Runtime

    try:
        config = load_config(path)
    except ConfigError as error:
        return ConfigCheck(path=config_path(path), problems=error.problems)
    registry = registry if registry is not None else Registry.default()

    problems: List[Problem] = []
    runtime = None
    try:
        runtime = Runtime.components(config, registry)
    except ConfigError as error:
        problems.extend(error.problems)
    secrets = _secret_statuses(config, registry)

    datasources: List["DatasourceStatus"] = []
    if runtime is not None:
        try:
            if connect:
                datasources = runtime.check_datasources()
                for i, status in enumerate(datasources):
                    if status.status == "failed":
                        file, inner = config.locate(f"datasources[{i}].connection")
                        problems.append(Problem(file=file, path=inner,
                                                message=f"'{status.id}' did not connect: {status.reason}",
                                                fix=status.fix))
        finally:
            runtime.close()
    return ConfigCheck(path=config_path(path), problems=problems, secrets=secrets, datasources=datasources)


def _secret_statuses(config: "Config", registry: "Registry") -> List[SecretStatus]:
    from nl2sql.secrets import SecretManager

    manager = SecretManager(registry)
    for provider in config.secrets.providers:
        try:
            manager.configure([provider])
        except ValueError:
            pass  # a problem from step 2b already; its references show as missing

    statuses = []
    for path, reference in _references(config, registry):
        provider, key = _REFERENCE.fullmatch(reference).groups()
        if provider == "env":
            present = bool(os.environ.get(key))
        else:
            try:
                present = bool(manager.resolve(reference))
            except Exception:
                present = False
        file, inner = config.locate(path)
        statuses.append(SecretStatus(reference=reference, file=file, path=inner, present=present))
    return statuses


def _references(config: "Config", registry: "Registry") -> Iterator[Tuple[str, str]]:
    """``(path, reference)`` for every secret the config uses.

    The ``${provider:key}`` references written in it, and the provider's
    standard key variable where the ``llm`` section names no ``api_key``.
    """
    yield from _walk(config, "")
    llm = config.llm
    defaults = [("llm.api_key", llm.provider, llm.api_key)]
    defaults += [(f"llm.agents.{name}.api_key", agent.provider, agent.api_key or llm.api_key)
                 for name, agent in llm.agents.items() if "provider" in agent.model_fields_set]
    for path, provider, api_key in defaults:
        if api_key is not None or not provider:
            continue
        try:
            variable = registry.load("llm", provider).api_key_env
        except Exception:
            continue  # an unknown provider: a problem from step 2b already
        if variable:
            yield path, f"${{env:{variable}}}"


def _walk(value: Any, path: str) -> Iterator[Tuple[str, str]]:
    if isinstance(value, str):
        if _REFERENCE.fullmatch(value):
            yield path, value
    elif isinstance(value, BaseModel):
        children = [(f.alias or name, getattr(value, name)) for name, f in type(value).model_fields.items()]
        children += list((value.model_extra or {}).items())  # a secret provider's own block
        for key, child in children:
            yield from _walk(child, f"{path}.{key}" if path else key)
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for i, child in enumerate(value):
            yield from _walk(child, f"{path}[{i}]")
