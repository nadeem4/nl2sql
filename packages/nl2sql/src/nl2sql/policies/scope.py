"""``Scope``: what one caller may read in one datasource, and ``PolicyEngine``, which builds it.

Every place that decides table access asks a ``Scope``: the datasource
resolver, the curated matcher, the schema retriever, the logical validator,
the executor's value check and ``engine.policies.allows()``. A run builds each
datasource's scope once and keeps it (``PolicyEngine.run``), so every step
decides the same way.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional, Tuple

from .models import UserContext
from .patterns import Pattern, parse_pattern

if TYPE_CHECKING:
    from nl2sql.config import Policies

# The default schema when the adapter cannot report one: the schema SQLite and
# DuckDB put every table in.
FALLBACK_DEFAULT_SCHEMA = "main"


@dataclass(frozen=True)
class RoleGrant:
    """One role's allow and deny patterns."""

    name: str
    allow: Tuple[Pattern, ...]
    deny: Tuple[Pattern, ...] = ()

    @classmethod
    def of(cls, name: str, allow, deny=()) -> "RoleGrant":
        return cls(name, tuple(parse_pattern(p) for p in allow), tuple(parse_pattern(p) for p in deny))

    def reaches(self, datasource_id: str) -> bool:
        return (any(p.covers_datasource(datasource_id) for p in self.allow)
                and not any(p.covers_all_of(datasource_id) for p in self.deny))


@dataclass(frozen=True)
class Scope:
    """What one caller may read in one datasource.

    A table is readable when *some* role allows it and that same role does not
    deny it: deny beats allow within a role, and roles add up. A table with no
    schema is in ``default_schema``, the adapter's (``main`` when it cannot
    say).
    """

    datasource_id: str
    grants: Tuple[RoleGrant, ...] = field(default=(), repr=False)
    default_schema: str = FALLBACK_DEFAULT_SCHEMA

    def reaches_datasource(self) -> bool:
        """Whether any role reaches this datasource: one of its allow patterns can match inside it."""
        return any(g.reaches(self.datasource_id) for g in self.grants)

    def allows(self, schema: Optional[str], table: str) -> bool:
        """Whether ``schema.table`` may be read."""
        return self.rule_for(schema, table) is None

    def rule_for(self, schema: Optional[str], table: str) -> Optional[str]:
        """Why ``schema.table`` may not be read (the deny, or the missing allow), for the log; ``None`` if it may."""
        schema = schema or self.default_schema
        denials = []
        for grant in self.grants:
            if not any(p.matches(self.datasource_id, schema, table) for p in grant.allow):
                continue
            deny = next((p for p in grant.deny if p.matches(self.datasource_id, schema, table)), None)
            if deny is None:
                return None
            denials.append(f"role '{grant.name}' denies '{deny.text}'")
        if denials:
            return "; ".join(denials)
        roles = ", ".join(g.name for g in self.grants) or "(none)"
        return f"no allow pattern of role(s) '{roles}' matches '{self.datasource_id}.{schema}.{table}'"


# What ``allow_all`` grants every caller.
_EVERYTHING = RoleGrant.of("allow_all", ["*"])

# The scopes of the run in this context, by caller and datasource (``PolicyEngine.run``).
_RUN_SCOPES: ContextVar[Optional[Tuple[threading.Lock, Dict[Any, Scope]]]] = ContextVar(
    "nl2sql_run_scopes", default=None)


def default_schema(ds_registry: Any, datasource_id: str) -> str:
    """The datasource's default schema as its adapter reports it, else ``main``."""
    try:
        value = getattr(ds_registry.get_adapter(datasource_id), "default_schema", None)
    except Exception:
        return FALLBACK_DEFAULT_SCHEMA
    return value if isinstance(value, str) and value else FALLBACK_DEFAULT_SCHEMA


class PolicyEngine:
    """Builds a caller's ``Scope`` from the ``policies`` section.

    ``ds_registry`` reports each datasource's default schema; without one,
    ``main`` stands in.
    """

    def __init__(self, policies: "Policies", ds_registry: Any = None):
        self.policies = policies
        self.ds_registry = ds_registry
        self._grants = {name: RoleGrant.of(name, role.allow, role.deny) for name, role in policies.roles.items()}

    def grants_for(self, user: Optional[UserContext]) -> Tuple[RoleGrant, ...]:
        """The grants of the caller's known roles.

        With ``allow_all`` every caller may read everything. A caller with no
        roles gets ``default_role``, when there is one. An unknown role grants
        nothing, and no roles at all grants nothing: both end in a normal
        refusal rather than an exception.
        """
        if self.policies.allow_all:
            return (_EVERYTHING,)
        default = self.policies.default_role
        roles = (user.roles if user else []) or ([default] if default else [])
        return tuple(self._grants[role] for role in roles if role in self._grants)

    def scope(self, user: Optional[UserContext], datasource_id: str) -> Scope:
        """What ``user`` may read in ``datasource_id``. Inside ``run()``, built once per caller and datasource."""
        pinned = _RUN_SCOPES.get()
        if pinned is None:
            return self._build(user, datasource_id)
        lock, scopes = pinned
        key = (user.model_dump_json() if user else None, datasource_id)
        with lock:
            if key not in scopes:
                scopes[key] = self._build(user, datasource_id)
            return scopes[key]

    def _build(self, user: Optional[UserContext], datasource_id: str) -> Scope:
        return Scope(datasource_id, self.grants_for(user), default_schema(self.ds_registry, datasource_id))

    @contextmanager
    def run(self) -> Iterator[None]:
        """Pins the scopes built in this context until it exits: one run decides one way."""
        token = _RUN_SCOPES.set((threading.Lock(), {}))
        try:
            yield
        finally:
            _RUN_SCOPES.reset(token)
