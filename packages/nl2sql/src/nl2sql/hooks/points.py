"""The six hook points and what a hook may answer at each (docs/architecture/wave_a.md, section 6).

The first three come before the logical validator and may modify what the
run carries; the last three come after it and may only stop it.
``test_hooks_modify_only_before_the_validator`` holds that boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet

from nl2sql.config.models import HookPoint
from nl2sql.hooks import Allow, Deny, Modify, NeedsApproval


@dataclass(frozen=True)
class PointRule:
    """What a hook may answer at one point.

    ``decisions`` are the decision types allowed there; ``modifies`` the
    fields a ``Modify`` may set; ``per_sub_query`` whether the point is called
    once per sub-query, so a ``Deny`` may be scoped to it.
    """

    decisions: FrozenSet[type]
    modifies: FrozenSet[str] = frozenset()
    per_sub_query: bool = False


HOOK_POINTS: Dict[HookPoint, PointRule] = {
    "run.before": PointRule(frozenset({Allow, Modify, Deny}), frozenset({"question"})),
    "datasources.after": PointRule(frozenset({Allow, Modify, Deny}), frozenset({"datasource_ids"})),
    "plan.after": PointRule(frozenset({Allow, Modify, Deny}), frozenset({"plan"}), per_sub_query=True),
    "execute.before": PointRule(frozenset({Allow, Deny, NeedsApproval}), per_sub_query=True),
    "execute.after": PointRule(frozenset({Allow, Deny}), per_sub_query=True),
    "answer.after": PointRule(frozenset({Allow, Deny})),
}
