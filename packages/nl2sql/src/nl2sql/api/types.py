"""The values the facade's namespaces return: plain dataclasses, and (Wave C) two pydantic models.

They import nothing heavy, so ``from nl2sql import DatasourceInfo`` costs
nothing at import (docs/architecture/invariants.md, the import rule).
``Candidate`` and ``ApiKey`` are pydantic models as the Wave C design writes them
(docs/architecture/wave_c.md, sections 7 and 9): both are also JSON on the wire.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple

from pydantic import Field

from nl2sql.common.strict import StrictModel
from nl2sql_sdk.discovery import Found


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


class Candidate(StrictModel):
    """A database `engine.datasources.scan()` found, with the connection fields it could fill in (D120).

    `connection` carries the URL's password: `repr` and `str` mask it, but `model_dump()` and
    `model_dump_json()` do not. Never log or serialise a Candidate; there is no REST route for scan (D121).
    """

    type: str  # the adapter's registry name
    title: str
    found: Found
    connection: Dict[str, Any]  # what was filled in; repr masks secret fields
    missing: List[str]  # required fields the form still needs
    installed: bool
    install_hint: str

    def __repr_args__(self) -> Iterator[Tuple[str, Any]]:
        from nl2sql.config.models import is_secret_key, masked

        for key, value in super().__repr_args__():
            if key == "connection" and isinstance(value, dict):
                value = {k: masked(v) if is_secret_key(k) else v for k, v in value.items()}
            yield key, value


class ApiKey(StrictModel):
    """An API key as `engine.api_keys` lists it: never the secret (D127)."""

    id: str  # 8 characters, shown in lists and logs
    label: str
    user_id: str
    tenant_id: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    created_at: datetime
    created_by: Optional[str] = None
    revoked_at: Optional[datetime] = None
