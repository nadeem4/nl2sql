"""The plan cache: a validated plan, pinned by the question it answered.

The planner is the one LLM call that decides the SQL; everything after it is
deterministic code. Reusing a plan that already passed validation and executed
therefore makes a repeated question produce the same SQL and the same rows, at
no planner cost.

The key is ``(normalised sub-query intent, run date and planner model,
datasource_id, schema_version)``:

* the sub-query intent, because the cache sits at the AST planner, which plans
  one sub-query at a time from its intent (not from the user's question);
* the run date (:func:`~nl2sql.common.dates.run_date`), because the planner
  is told today's date and writes "this year" or "the last 30 days" as literal
  dates: a plan from yesterday would answer for yesterday's window. Every
  entry is keyed on it, not just date-relative ones, because no test of the
  intent or the plan can tell reliably which answers depend on today. So a
  plan is reused for the rest of the day it was made, or for as long as
  ``settings.pipeline.today`` pins the date;
* the planner's provider and model (the ``ast_planner`` agent's, as the run
  sees it), because another model may plan the same question differently:
  after ``engine.llm.replace`` a plan written by the old model is never
  served. Its entries stay, and swapping back hits them again;
* the datasource, because the same words mean different tables elsewhere;
* the schema version, so a rebuilt index with a changed schema misses.

Matching is exact after :func:`normalize_question`, never by similarity.

A cached plan is never trusted: the logical validator, generator and executor
run on it every time, so a policy change or a less privileged role still
refuses it. Only plans that validated and executed are stored (by the sub-query
wrapper, which knows the outcome). Entries live in the schema store
(``state.db`` under ``storage.path``), and ``settings.pipeline.plan_cache: false``
turns reads and writes off.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from nl2sql.common.logger import get_logger
from nl2sql.common.metrics import plan_cache_counter
from nl2sql.common.dates import run_date
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel

logger = get_logger("plan_cache")

_WHITESPACE = re.compile(r"\s+")
_TRAILING = re.compile(r"[\s.?!,;:]+$")

# The agent whose model writes the plans (``LLM_AGENTS["ast_planner"]``).
PLANNER_AGENT = "astplanner"


def normalize_question(text: str) -> str:
    """Case-fold, collapse whitespace and strip trailing punctuation. Nothing else."""
    folded = _WHITESPACE.sub(" ", (text or "").casefold()).strip()
    return _TRAILING.sub("", folded)


class PlanCache:
    """Reads and writes validated plans in the schema store.

    Every failure (no store, a store without a cache, an unreadable entry) is a
    miss: the planner then runs as if there were no cache. ``enabled`` and
    ``today`` are the run's ``settings.pipeline.plan_cache`` and
    ``settings.pipeline.today``; ``planner`` is the run's planner model, as
    ``provider/model``.
    """

    def __init__(self, store: Any, *, enabled: bool = True, today: Optional[date] = None,
                 planner: Optional[str] = None):
        self._store = store
        self._enabled = enabled
        self._today = today
        self._planner = planner

    def _usable(self) -> bool:
        return self._enabled and hasattr(self._store, "get_cached_plan")

    def _key(self, sub_query: Any) -> Optional[tuple]:
        if sub_query is None or not sub_query.schema_version:
            # Without a version nothing would invalidate the entry.
            return None
        question = normalize_question(sub_query.intent)
        if not question:
            return None
        # The same intent ranked or cut differently is a different plan. Keyed
        # only when present, so entries for plain intents keep hitting.
        order_by = getattr(sub_query, "order_by", None) or []
        limit = getattr(sub_query, "limit", None)
        if order_by:
            question += " | order_by=" + ",".join(f"{o.attribute} {o.direction}" for o in order_by)
        if limit is not None:
            question += f" | limit={limit}"
        # The planner resolves relative dates against the run date.
        question += f" | today={run_date(self._today).isoformat()}"
        if self._planner:
            question += f" | planner={self._planner}"
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


def plan_cache_for(ctx: Any) -> PlanCache:
    """The plan cache of a runtime: its schema store, its ``settings.pipeline`` and its planner model.

    Built while a run's graph is built, so the planner model is the one that
    run uses (``LLMRegistry.effective``): the planner node reads and the
    sub-query wrapper writes under one key.
    """
    pipeline = ctx.settings.pipeline
    effective = getattr(getattr(ctx, "llm_registry", None), "effective", None)
    planner = effective(PLANNER_AGENT) if callable(effective) else None
    return PlanCache(getattr(ctx, "schema_store", None), enabled=pipeline.plan_cache, today=pipeline.today,
                     planner=planner)
