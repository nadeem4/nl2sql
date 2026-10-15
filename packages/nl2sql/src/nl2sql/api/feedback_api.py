"""``engine.feedback``: answer ratings, the guardrail rates, and draft eval cases.

The ratings live in the engine's state store (``storage.state``,
``<storage.path>/state.db`` by default); the rates also read the run traces
under ``<storage.path>/traces``. ``nl2sql.feedback`` is imported inside each
method, so answering a question never loads it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from nl2sql.runtime import Runtime


class FeedbackAPI:
    """``engine.feedback``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

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
        from nl2sql.feedback import rated_row, run_record, run_signals
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
        return self._ctx.state.add_feedback(rated_row(row, rating, note))

    def list(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Every rating, newest first; at most ``limit`` of them."""
        return self._ctx.state.list_feedback(limit=limit)

    def stats(self, *, traces: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
        """Rates over recorded runs: the ratings plus the run traces in ``traces``.

        ``traces`` defaults to the engine's own traces folder, the ``files``
        store's (with another trace store, only the ratings count). Each run counts
        once. Reports the feedback, refusals, refiner retries, validator
        failures and errors (each as runs and a rate over all runs), and the
        plan cache's hit rate over planned sub-queries.
        """
        from nl2sql.feedback import compute_stats, load_traces

        folder = Path(traces) if traces is not None else getattr(self._ctx.traces, "directory", None)
        return compute_stats(self.list(), load_traces(folder) if folder is not None else [])

    def export(self, path: Union[str, Path]) -> Dict[str, int]:
        """Write the thumbs-up runs to ``path`` as draft eval cases (``status: draft``, ``origin: feedback``).

        A person reviews each one, sets ``status: approved`` and runs
        ``engine.evals.regenerate``. A run with no SQL, or more than one
        statement, is skipped: a case's ``sql`` is one query. A suite the
        package ships is never written.

        Returns:
            ``{"written": <drafts>, "skipped": <runs>}``.

        Raises:
            ValueError: ``path`` is in the package's own suites folder.
        """
        from nl2sql.evaluation.suite import SUITES_DIR
        from nl2sql.feedback import draft_entries, write_drafts

        path = Path(path)
        if path.resolve().parent == SUITES_DIR.resolve():
            raise ValueError("Drafts never go into a suite the package ships. Write them to another file.")
        drafts, skipped = draft_entries(self.list())
        write_drafts(drafts, path)
        return {"written": len(drafts), "skipped": skipped}

    def clear(self) -> int:
        """Delete every rating; returns how many. Schema snapshots and the plan cache stay."""
        return self._ctx.state.clear_feedback()

    def _llm_configs(self) -> Dict[str, Any]:
        """Provider and model per agent, by the name each model step asks for."""
        registry = getattr(self._ctx, "llm_registry", None)
        return registry.list_llms() if registry is not None else {}

    def _trace(self, trace_id: str) -> Optional[Dict[str, Any]]:
        from nl2sql.tracing.document import validate_trace_id

        try:
            return self._ctx.traces.get(validate_trace_id(trace_id))
        except (ValueError, OSError):
            return None
