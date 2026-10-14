"""The values the facade's namespaces return, as plain dataclasses.

They import nothing heavy, so ``from nl2sql import DatasourceInfo`` costs
nothing at import (docs/architecture/invariants.md, the import rule).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass(frozen=True)
class DatasourceInfo:
    """One configured datasource, as ``engine.datasources.list()`` and ``get()`` report it.

    ``status`` is ``ready`` once ``check()`` (or ``datasources.test``)
    connected to it, ``failed`` when it did not (``reason`` says why), and
    ``unchecked`` before either ran. ``connection`` is the configured
    connection with every secret masked. A failed datasource has no
    ``capabilities``: nothing is routed to it.
    """

    id: str
    type: str
    description: Optional[str]
    status: Literal["ready", "failed", "unchecked"]
    capabilities: List[str] = field(default_factory=list)
    connection: Dict[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None


@dataclass(frozen=True)
class DatasourceType:
    """One adapter the engine's registry can name, as ``engine.datasources.types()`` reports it.

    ``installed`` is whether every one of its driver modules is found; when
    one is not, ``install_hint`` says what to install. ``connection_schema``
    is the JSON Schema of its connection model: the Connect a database form.
    A secret field is ``"format": "password", "writeOnly": true``, and a field
    in the ``advanced`` group carries ``"group": "advanced"``.
    """

    name: str
    title: str
    installed: bool
    install_hint: str
    connection_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IndexProgress:
    """One datasource's step in ``engine.index.build``, handed to its ``on_datasource`` callback.

    ``state`` is ``started``, then ``done`` with ``counts`` (the entries
    written, by kind) or ``failed`` with ``reason``. The same value serves a
    console line and a job's status record.
    """

    datasource_id: str
    state: Literal["started", "done", "failed"]
    counts: Dict[str, int] = field(default_factory=dict)
    reason: Optional[str] = None
