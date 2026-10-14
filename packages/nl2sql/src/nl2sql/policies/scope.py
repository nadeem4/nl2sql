"""``Scope``: what one caller may read in one datasource, and ``PolicyEngine``, which builds it.

Every place that decides table access asks a ``Scope``: the datasource
resolver, the curated matcher, the schema retriever, the logical validator,
the executor's value check and ``engine.policies.allows()``. A run builds each
datasource's scope once and keeps it (``PolicyEngine.run``), so every step
decides the same way.

An optional policy check (``nl2sql_sdk.policy.PolicyCheck``) is asked once per
caller and datasource when the scope is built. Its ``Grant`` is intersected
with the roles, and any exception from it denies the datasource: it fails
closed.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Iterator, Optional, Tuple

from nl2sql_sdk.policy import Grant, UserContext

from .patterns import Pattern, parse_pattern

if TYPE_CHECKING:
    from nl2sql.config import Policies

# The default schema when the adapter cannot report one: the schema SQLite and
# DuckDB put every table in.
FALLBACK_DEFAULT_SCHEMA = "main"

logger = logging.getLogger(__name__)


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
    deny it: deny beats allow within a role, and roles add up. With a policy
    check, the table must also be allowed, and not denied, by ``check``, the
    check's grant: neither side can widen the other. A table with no schema
    is in ``default_schema``, the adapter's (``main`` when it cannot say).
    """

    datasource_id: str
    grants: Tuple[RoleGrant, ...] = field(default=(), repr=False)
    default_schema: str = FALLBACK_DEFAULT_SCHEMA
    check: Optional[RoleGrant] = field(default=None, repr=False)

    def reaches_datasource(self) -> bool:
        """Whether any role reaches this datasource, and the check (if any) does too."""
        return (any(g.reaches(self.datasource_id) for g in self.grants)
                and (self.check is None or self.check.reaches(self.datasource_id)))

    def allows(self, schema: Optional[str], table: str) -> bool:
        """Whether ``schema.table`` may be read."""
        return self.rule_for(schema, table) is None

    def rule_for(self, schema: Optional[str], table: str) -> Optional[str]:
        """Why ``schema.table`` may not be read (the deny, or the missing allow), for the log; ``None`` if it may."""
        schema = schema or self.default_schema
        return self._role_rule(schema, table) or self._check_rule(schema, table)

    def _role_rule(self, schema: str, table: str) -> Optional[str]:
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

    def _check_rule(self, schema: str, table: str) -> Optional[str]:
        check = self.check
        if check is None:
            return None
        if not any(p.matches(self.datasource_id, schema, table) for p in check.allow):
            return f"{check.name} does not allow '{self.datasource_id}.{schema}.{table}'"
        deny = next((p for p in check.deny if p.matches(self.datasource_id, schema, table)), None)
        return f"{check.name} denies '{deny.text}'" if deny is not None else None


# What ``allow_all`` grants every caller.
_EVERYTHING = RoleGrant.of("allow_all", ["*"])

# The scopes of the run in this context, by caller and datasource (``PolicyEngine.run``).
_RUN_SCOPES: ContextVar[Optional[Tuple[threading.Lock, Dict[Any, Scope]]]] = ContextVar(
    "nl2sql_run_scopes", default=None)


def default_schema(ds_registry: Any, datasource_id: str) -> str:
    """The datasource's default schema as its adapter reports it, else ``main``."""
    try:
        value = ds_registry.get_adapter(datasource_id).default_schema()
    except Exception:
        return FALLBACK_DEFAULT_SCHEMA
    return value if isinstance(value, str) and value else FALLBACK_DEFAULT_SCHEMA


class PolicyEngine:
    """Builds a caller's ``Scope`` from the ``policies`` section.

    ``ds_registry`` reports each datasource's default schema; without one,
    ``main`` stands in. ``check`` is the policy check, built: the runtime
    builds a ``policies.check`` block through the registry. Without it, a
    check object in ``policies.check`` is used as given.
    """

    def __init__(self, policies: "Policies", ds_registry: Any = None, check: Any = None):
        self.policies = policies
        self.ds_registry = ds_registry
        self._grants = {name: RoleGrant.of(name, role.allow, role.deny) for name, role in policies.roles.items()}
        configured = policies.check
        if check is None and configured is not None:
            if not callable(getattr(configured, "grant", None)):
                raise ValueError(f"The policy check '{configured.type}' must be built through the registry "
                                 "and passed as check=")
            check = configured
        self.check = check
        self.check_name = getattr(configured, "type", None) or type(check).__name__

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
        return Scope(datasource_id, self.grants_for(user), default_schema(self.ds_registry, datasource_id),
                     self._check_grant(user, datasource_id))

    def _check_grant(self, user: Optional[UserContext], datasource_id: str) -> Optional[RoleGrant]:
        """The check's grant for ``user`` in ``datasource_id``; on any failure, a grant of nothing."""
        if self.check is None:
            return None
        name = f"policy check '{self.check_name}'"
        try:
            grant = self.check.grant(user or UserContext(), datasource_id)
            if not isinstance(grant, Grant):
                raise TypeError(f"grant() returned {type(grant).__name__}, not a Grant")
            return RoleGrant.of(name, grant.allow, grant.deny)
        except Exception as e:
            logger.warning("The %s failed for datasource '%s', so the datasource is denied for this run: %s: %s",
                           name, datasource_id, type(e).__name__, e)
            return RoleGrant(f"{name} (failed: {type(e).__name__})", ())

    @contextmanager
    def run(self) -> Iterator[None]:
        """Pins the scopes built in this context until it exits: one run decides one way."""
        token = _RUN_SCOPES.set((threading.Lock(), {}))
        try:
            yield
        finally:
            _RUN_SCOPES.reset(token)
