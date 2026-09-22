"""The plan cache: a validated plan, pinned by the question it answered.

The planner is the one LLM call that decides the SQL; everything after it is
deterministic code. Reusing a plan that already passed validation and executed
therefore makes a repeated question produce the same SQL and the same rows, at
no planner cost.

The key is ``(normalised sub-query intent, datasource_id, schema_version)``:

* the sub-query intent, because the cache sits at the AST planner, which plans
  one sub-query at a time from its intent (not from the user's question);
* the datasource, because the same words mean different tables elsewhere;
* the schema version, so a rebuilt index with a changed schema misses.

Matching is exact after :func:`normalize_question`, never by similarity.

A cached plan is never trusted: the logical validator, generator and executor
run on it every time, so a policy change or a less privileged role still
refuses it. Only plans that validated and executed are stored (by the sub-query
wrapper, which knows the outcome). Entries live in the schema store
(``schema_store.db`` for the sqlite backend), and ``PLAN_CACHE_ENABLED=false``
turns reads and writes off.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from nl2sql.common.logger import get_logger
from nl2sql.common.metrics import plan_cache_counter
from nl2sql.common.settings import settings
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel

logger = get_logger("plan_cache")

_WHITESPACE = re.compile(r"\s+")
_TRAILING = re.compile(r"[\s.?!,;:]+$")


def normalize_question(text: str) -> str:
    """Case-fold, collapse whitespace and strip trailing punctuation. Nothing else."""
    folded = _WHITESPACE.sub(" ", (text or "").casefold()).strip()
    return _TRAILING.sub("", folded)


class PlanCache:
    """Reads and writes validated plans in the schema store.

    Every failure (no store, a store without a cache, an unreadable entry) is a
    miss: the planner then runs as if there were no cache.
    """

    def __init__(self, store: Any):
        self._store = store

    def _usable(self) -> bool:
        return bool(settings.plan_cache_enabled) and hasattr(self._store, "get_cached_plan")

    @staticmethod
    def _key(sub_query: Any) -> Optional[tuple]:
        if sub_query is None or not sub_query.schema_version:
            # Without a version nothing would invalidate the entry.
            return None
        question = normalize_question(sub_query.intent)
        if not question:
            return None
        return question, sub_query.datasource_id, sub_query.schema_version

    def get(self, sub_query: Any) -> Optional[PlanModel]:
        """The cached plan for this sub-query, or None."""
        if not self._usable():
            return None
        key = self._key(sub_query)
        if key is None:
            return None
        try:
            raw = self._store.get_cached_plan(*key)
            plan = PlanModel.model_validate_json(raw) if raw else None
        except Exception as exc:
            logger.warning("Ignoring an unreadable cached plan: %s", exc)
            plan = None
        plan_cache_counter.add(1, attributes={"result": "hit" if plan else "miss",
                                              "datasource_id": str(key[1])})
        return plan

    def put(self, sub_query: Any, plan: PlanModel) -> bool:
        """Stores a plan that validated and executed. Returns whether it was stored."""
        if not self._usable() or plan is None:
            return False
        key = self._key(sub_query)
        if key is None:
            return False
        try:
            self._store.put_cached_plan(*key, plan.model_dump_json())
        except Exception as exc:
            logger.warning("Could not cache the plan: %s", exc)
            return False
        return True

    def clear(self) -> int:
        """Removes every cached plan; returns how many there were."""
        if not hasattr(self._store, "clear_plan_cache"):
            return 0
        return self._store.clear_plan_cache()
