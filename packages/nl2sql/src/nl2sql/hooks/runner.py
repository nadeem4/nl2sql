"""``HookRunner``: runs an engine's hooks at a point and says how the point ended.

The runtime builds the list (``build_hook`` for each entry of the ``hooks``
section, in order) and hands it to the runner, so a built-in hook such as
Wave B's ``policy.cost`` can be put first with approvers of its own. The
runner reads no config. The rules (docs/architecture/wave_a.md, section 6):

- Hooks run in order. A ``Modify`` replaces the fields it sets and the next
  hook sees the result; the first ``Deny`` or ``NeedsApproval`` ends the
  point.
- A hook error denies with ``HOOK_FAILED`` (hooks fail closed): an
  exception, a value that is not a decision, or a decision the point does not
  allow. ``on_error: open`` turns an exception, and only an exception, into
  an allow: a disallowed decision is a bug in the hook, not an outage.
- A ``NeedsApproval`` that one of the run's approvals covers counts as
  ``Allow``, and the next hook still runs (D24). The runner checks the
  approval (``approval_problem``) against the hook's configured approver
  roles, by default ``policies.admin_roles``.
- Every hook asked publishes ``hook.decision`` on the run's scope.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, FrozenSet, Literal, Optional, Sequence, Tuple

from pydantic import ValidationError

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.logger import get_logger
from nl2sql.config.models import HookConfig, HookPoint
from nl2sql.events import publish
from nl2sql.hooks import Allow, Deny, HookContext, Modify, NeedsApproval
from nl2sql.hooks.approval import Approval, approval_problem
from nl2sql.hooks.points import HOOK_POINTS

if TYPE_CHECKING:
    from nl2sql.registry import Registry
    from nl2sql.secrets import SecretManager

logger = get_logger("hooks")

_DECISIONS = (Allow, Modify, Deny, NeedsApproval)
_EVENT_NAMES = {Allow: "allow", Modify: "modify", Deny: "deny", NeedsApproval: "needs_approval"}


@dataclass(frozen=True)
class HookBinding:
    """One hook as the runner runs it: its name, the callable, where it runs and how it fails.

    ``approver_roles`` may approve its ``NeedsApproval``; None means
    ``policies.admin_roles`` (read when approvals are checked).
    """

    name: str
    hook: Callable[[HookContext], Any]
    points: FrozenSet[HookPoint]
    on_error: Literal["closed", "open"] = "closed"
    approver_roles: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class HookVerdict:
    """How a point ended: ``ctx`` after every ``Modify``, and the decision that stopped it, if one did.

    ``decision`` is None when the run may go on. Otherwise it is the ``Deny``
    or ``NeedsApproval`` that ended the point, from hook ``hook``; ``failed``
    marks a hook error (always a run-scoped ``Deny``). ``modified`` is True
    when a ``Modify`` changed a field: then read the changed value from ``ctx``.
    """

    ctx: HookContext
    decision: Optional[Any] = None
    hook: Optional[str] = None
    failed: bool = False
    modified: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision is None

    @property
    def scope(self) -> Optional[str]:
        """``run`` or ``sub_query`` for a deny, ``run`` for an approval; None when allowed."""
        if self.decision is None:
            return None
        return self.decision.scope if isinstance(self.decision, Deny) else "run"

    def error(self, node: str) -> PipelineError:
        """The run's error for a stopped point: ``HOOK_DENIED``, ``HOOK_FAILED`` or ``NEEDS_APPROVAL``.

        Never retried (``CRITICAL``). ``details`` name the hook and the point,
        and a deny's scope. A hook error names only the hook and the point: the
        exception text may hold an internal URL or a token, so it goes to the
        log and the ``hook.decision`` event, which are operator data.
        """
        details = {"hook": self.hook, "point": self.ctx.point}
        if self.failed:
            code, message = ErrorCode.HOOK_FAILED, f"The hook '{self.hook}' failed at {self.ctx.point}."
        elif isinstance(self.decision, NeedsApproval):
            code, message = ErrorCode.NEEDS_APPROVAL, (f"This question needs approval before it runs: "
                                                       f"{self.decision.reason}")
            details["reason"] = self.decision.reason
        else:
            code, message = ErrorCode.HOOK_DENIED, self.decision.reason
            details["scope"] = self.decision.scope
        return PipelineError(node=node, message=message, severity=ErrorSeverity.CRITICAL, error_code=code,
                             details=details)


class HookRunner:
    """Runs ``bindings``, in order, at each point. Built once per engine by the runtime.

    ``admin_roles`` (``policies.admin_roles``) approve for a hook configured
    with no ``approver_roles`` of its own.
    """

    def __init__(self, bindings: Sequence[HookBinding], admin_roles: Sequence[str] = ()) -> None:
        self.bindings: Tuple[HookBinding, ...] = tuple(bindings)
        self.admin_roles: Tuple[str, ...] = tuple(admin_roles)

    def approver_roles(self, hook: str) -> Tuple[str, ...]:
        """The roles that may approve hook ``hook``'s ``NeedsApproval``."""
        for binding in self.bindings:
            if binding.name == hook:
                return binding.approver_roles if binding.approver_roles is not None else self.admin_roles
        return ()

    def attached(self, point: HookPoint) -> bool:
        """Whether any hook runs at ``point``."""
        return any(point in binding.points for binding in self.bindings)

    def call(self, point: HookPoint, **fields: Any) -> HookVerdict:
        """Ask every hook attached at ``point``; ``fields`` are the ``HookContext``'s, ``point`` aside.

        The hooks see deep copies of ``fields``: the plan, the caller and the
        values are the run's own objects, and a hook that changed one in place
        would change the run without a ``Modify`` (past the validator, or into
        the plan cache). ``Modify`` is the only way a hook changes anything.
        """
        rule = HOOK_POINTS[point]
        attached = [binding for binding in self.bindings if point in binding.points]
        if not attached:
            # Nothing to ask: the values go on as given, unchecked.
            return HookVerdict(HookContext.model_construct(point=point, **fields))
        ctx = HookContext(point=point, **copy.deepcopy(fields))
        modified = False
        for binding in attached:
            started = time.perf_counter()
            try:
                decision = binding.hook(ctx)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                _published(binding, ctx, "error", reason, started)
                if binding.on_error == "open":
                    logger.warning(f"The hook '{binding.name}' failed at {point} and is configured "
                                   f"on_error: open, so the run goes on: {reason}")
                    continue
                return _failed(binding, ctx, reason)
            problem = _problem(decision, ctx, rule)
            if problem:
                _published(binding, ctx, "error", problem, started)
                return _failed(binding, ctx, problem)
            if isinstance(decision, NeedsApproval):
                approval = self._covering(binding, ctx)
                if approval is not None:
                    # Approved counts as Allow: the next hook is still asked.
                    _published(binding, ctx, "approved", decision.reason, started, approver=approval.by.user_id)
                    continue
            _published(binding, ctx, _EVENT_NAMES[type(decision)], getattr(decision, "reason", None), started)
            if isinstance(decision, Modify):
                changes = {name: getattr(decision, name) for name in rule.modifies
                           if getattr(decision, name) is not None}
                # Copied too: the hook keeps no handle on what the run goes on with.
                ctx = ctx.model_copy(update=copy.deepcopy(changes))
                modified = modified or bool(changes)
            elif isinstance(decision, (Deny, NeedsApproval)):
                return HookVerdict(ctx, decision, binding.name, modified=modified)
        return HookVerdict(ctx, modified=modified)

    def _covering(self, binding: HookBinding, ctx: HookContext) -> Optional[Approval]:
        """The run's approval that covers ``binding``'s ``NeedsApproval`` here, or None."""
        roles = self.approver_roles(binding.name)
        for approval in ctx.approvals:
            problem = approval_problem(approval, hook=binding.name, roles=roles, question=ctx.question,
                                       user=ctx.user, datasource_id=ctx.datasource_id, sql=ctx.sql,
                                       params=ctx.params)
            if problem is None:
                return approval
            logger.warning(f"An approval was not accepted for the hook '{binding.name}': {problem}.")
        return None


NO_HOOKS = HookRunner(())


def hooks_of(ctx: Any) -> HookRunner:
    """The runtime's runner; none for a stand-in runtime that has no ``hooks``."""
    return getattr(ctx, "hooks", None) or NO_HOOKS


def _problem(decision: Any, ctx: HookContext, rule) -> Optional[str]:
    """Why ``decision`` is a hook error at ``ctx.point``, or None when it is allowed there."""
    if not isinstance(decision, _DECISIONS):
        return f"it returned {type(decision).__name__}, not a decision (Allow, Modify, Deny or NeedsApproval)"
    if type(decision) not in rule.decisions:
        allowed = ", ".join(sorted(t.__name__ for t in rule.decisions))
        return f"{type(decision).__name__} is not allowed at {ctx.point} (allowed: {allowed})"
    if isinstance(decision, Modify):
        changed = {name for name in Modify.model_fields if getattr(decision, name) is not None}
        if changed - rule.modifies:
            return f"Modify may set only {', '.join(sorted(rule.modifies))} at {ctx.point}"
        if decision.datasource_ids is not None:
            added = set(decision.datasource_ids) - set(ctx.datasource_ids or [])
            if added:
                return f"datasources.after may only remove datasources; it added {', '.join(sorted(added))}"
    if isinstance(decision, Deny) and decision.scope == "sub_query" and not rule.per_sub_query:
        return f"a deny at {ctx.point} cannot be scoped to a sub-query"
    return None


def _failed(binding: HookBinding, ctx: HookContext, reason: str) -> HookVerdict:
    logger.error(f"The hook '{binding.name}' failed at {ctx.point}, so it denies: {reason}")
    return HookVerdict(ctx, Deny(reason=reason), binding.name, failed=True)


def _published(binding: HookBinding, ctx: HookContext, decision: str, reason: Optional[str],
               started: float, approver: Optional[str] = None) -> None:
    publish("hook.decision", {
        "hook": binding.name,
        "point": ctx.point,
        "decision": decision,
        "reason": reason,
        "sub_query_id": ctx.sub_query.id if ctx.sub_query is not None else None,
        "approver": approver,
        "seconds": round(time.perf_counter() - started, 6),
    })


# ---------------------------------------------------------------- building


def build_hook(entry: Any, *, registry: "Registry", secrets: "SecretManager") -> HookBinding:
    """One entry of the ``hooks`` section as a ``HookBinding``.

    A ``HookConfig`` names a registered hook: it is loaded through the
    registry, its own keys (``${provider:key}`` references resolved) are
    validated by its ``config_model``, and it is built with ``from_config``.
    Its ``points`` must be among the hook's own. An object is used as given,
    at its ``points`` (all six when it declares none), named by its ``name``
    or its type's (a function's) name.

    Raises:
        RegistryError: the hook's ``type`` is unknown or cannot be loaded.
        ValueError: a point the hook does not declare, or keys that are not valid.
    """
    if not isinstance(entry, HookConfig):
        name = getattr(entry, "name", None) or getattr(entry, "__name__", None) or type(entry).__name__
        return HookBinding(name=name, hook=entry, points=frozenset(getattr(entry, "points", None) or HOOK_POINTS))
    cls = registry.load("hook", entry.type)
    declared = frozenset(getattr(cls, "points", None) or HOOK_POINTS)
    points = frozenset(entry.points) if entry.points is not None else declared
    if points - declared:
        raise ValueError(f"The {entry.type} hook cannot run at {', '.join(sorted(points - declared))}: it runs at "
                         f"{', '.join(sorted(declared))}.")
    keys = secrets.resolve_object(dict(entry.model_extra or {}))
    try:
        cfg = cls.config_model.model_validate(keys)
    except ValidationError as e:
        # Each key and what is wrong with it; never the value, which may be a secret.
        reasons = "; ".join(f"{'.'.join(str(p) for p in error['loc']) or 'the block'}: {error['msg']}"
                            for error in e.errors())
        raise ValueError(f"The {entry.type} hook's keys are not valid: {reasons}.") from None
    roles = tuple(entry.approver_roles) if entry.approver_roles is not None else None
    return HookBinding(name=entry.type, hook=cls.from_config(cfg), points=points, on_error=entry.on_error,
                       approver_roles=roles)
