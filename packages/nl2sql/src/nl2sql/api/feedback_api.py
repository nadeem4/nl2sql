"""``engine.feedback``: answer ratings, the guardrail rates, and draft gold entries.

The ratings live in the ``feedback`` table of the engine's state store,
``<storage.path>/state.db``; the rates also read the run traces under
``<storage.path>/traces``. ``nl2sql.feedback`` is imported inside each method,
so answering a question never loads it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from nl2sql.runtime import Runtime


class FeedbackAPI:
    """``engine.feedback``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def _store(self):
        from nl2sql.feedback import FeedbackStore

        return FeedbackStore(self._ctx.stores.state)

    def record(self, result: Union[Mapping[str, Any], Any], *, question: str, role: str, rating: str,
               note: Optional[str] = None) -> Dict[str, Any]:
        """Rate one run: ``"up"`` or ``"down"``, with an optional note (cut to 280 characters).

        ``result`` is the run's ``QueryResult``, or its JSON. What is stored is
        built from it and from the run's trace, when one was kept (the trace
        also sees validator rejections a retry fixed): the question, the role,
        the status, the SQL, the datasources, the error codes, the counters,
        the provider and model each model step ran on, and the engine version.
        Never rows, the written answer, error messages or keys; a run refused
        for the role keeps no SQL. A later rating of the same run replaces the
        earlier one.

        Returns:
            The stored row.

        Raises:
            ValueError: ``rating`` is not ``"up"`` or ``"down"``, or the result has no ``trace_id``.
        """
        from nl2sql.feedback import run_record, run_signals
        from nl2sql.tracing.trace import engine_info

        body = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        if not body.get("trace_id"):
            raise ValueError("Only a run with a trace_id can be rated.")
        info = engine_info()
        version = f"{info['version']} ({info['git_sha']})" if info.get("git_sha") else str(info["version"])
        row = run_record(body, question=question, role=role, llm_configs=self._llm_configs(),
                         engine_version=version)
        trace = self._trace(row["trace_id"])
        if trace is not None:
            row.update(run_signals(trace.get("result") or {}, trace))
            if "SECURITY_VIOLATION" in row["error_codes"]:
                row["sql"] = []
        store = self._store()
        try:
            return store.save(row, rating=rating, note=note)
        finally:
            store.close()

    def list(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Every rating, newest first; at most ``limit`` of them."""
        store = self._store()
        try:
            return store.list(limit=limit)
        finally:
            store.close()

    def stats(self, *, traces: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """Rates over recorded runs: the ratings plus the run traces in ``traces``.

        ``traces`` defaults to the engine's own traces folder. Each run counts
        once. Reports the feedback, refusals, refiner retries, validator
        failures and errors (each as runs and a rate over all runs), and the
        plan cache's hit rate over planned sub-queries.
        """
        from nl2sql.feedback import compute_stats, load_traces

        folder = Path(traces) if traces is not None else self._ctx.traces_dir
        return compute_stats(self.list(), load_traces(folder))

    def export(self, path: Union[str, Path]) -> Dict[str, int]:
        """Write the thumbs-up runs to ``path`` as draft gold entries, for a person to review.

        A run with no SQL, or more than one statement, is skipped: ``gold_sql``
        is one query. The gold set itself is never written.

        Returns:
            ``{"written": <drafts>, "skipped": <runs>}``.

        Raises:
            ValueError: ``path`` is the gold set.
        """
        from nl2sql.evaluation.gold import GOLD_DATASET_PATH
        from nl2sql.feedback import draft_entries, write_drafts

        path = Path(path)
        if path.resolve() == GOLD_DATASET_PATH.resolve():
            raise ValueError("Drafts never go into the gold set itself. Write them to another file.")
        drafts, skipped = draft_entries(self.list())
        write_drafts(drafts, path)
        return {"written": len(drafts), "skipped": skipped}

    def clear(self) -> int:
        """Delete every rating; returns how many. Schema snapshots and the plan cache stay."""
        store = self._store()
        try:
            return store.clear()
        finally:
            store.close()

    def _llm_configs(self) -> Dict[str, Any]:
        """Provider and model per agent, by the name each model step asks for."""
        registry = getattr(self._ctx, "llm_registry", None)
        return registry.list_llms() if registry is not None else {}

    def _trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        from nl2sql.api.traces_api import read_trace

        try:
            return read_trace(self._ctx.traces_dir, trace_id)
        except (ValueError, OSError):
            return None
