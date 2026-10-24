"""``engine.feedback``: answer ratings, the guardrail rates, and draft eval cases.

The ratings live in the engine's state store (``storage.state``,
``<storage.path>/state.db`` by default); the rates also read the run traces
under ``<storage.path>/traces``. ``nl2sql.feedback`` is imported inside each
method, so answering a question never loads it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Union

from nl2sql.common.logger import get_logger
from nl2sql.runtime import Runtime

if TYPE_CHECKING:
    from nl2sql_sdk.stores import Proposal

    from nl2sql.policies import UserContext

logger = get_logger(__name__)


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

        A thumbs-up on a generated answer of one datasource with one read-only
        SQL statement also files a ``verified_query`` proposal in the review
        queue (``engine.proposals``): the question, the run's SQL and an id
        from the question. The same change is one proposal however often it
        is rated; it reaches ``curated.yaml`` only when an admin accepts it.

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
        stored = self._ctx.state.add_feedback(rated_row(row, rating, note))
        try:
            self._propose(stored)
        except Exception as e:  # the rating is stored; a queue that cannot take the proposal never fails it
            logger.warning("The thumbs-up of run %s was stored, but its verified-query proposal was not filed: %s",
                           stored.get("trace_id"), e)
        return stored

    def correct(self, trace_id: str, *, sql: Optional[str] = None, plan: Any = None, question: Optional[str] = None,
                note: Optional[str] = None, user_context: "UserContext") -> "Proposal":
        """Correct run ``trace_id`` with a fix, checked, as a ``verified_query`` proposal (wave_b.md, section 10).

        Exactly one of ``sql`` (one read-only query) or ``plan`` (a
        ``PlanModel`` or its JSON) is the fix. The run is rated ``down`` with
        ``note`` unless it already is. The fix is checked as ``user_context``
        before it is queued: a plan through ``plan.after``, the validator and
        the generator; the SQL read-only, within the caller's scope, through
        the ``execute.before`` hooks, and run once. It reaches ``curated.yaml``
        only when an admin accepts it (``engine.proposals.accept``).

        Returns:
            The ``Proposal`` (origin ``correction``) as the queue stored it.

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``.
            ValueError: not exactly one of ``sql`` and ``plan``.
            CorrectionRefused: a check failed; its ``code`` and ``messages`` say why. A ``ValueError``.
        """
        from nl2sql.feedback.corrections import correct

        return correct(self._ctx, trace_id, sql=sql, plan=plan, question=question, note=note,
                       user_context=user_context, rate=self.record)

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
        statement, is skipped: a case's ``sql`` is one query. Drafts never
        replace reviewed cases: a suite the package ships, a suite
        ``evals.yaml`` names, and any existing file holding an approved case
        are refused.

        Returns:
            ``{"written": <drafts>, "skipped": <runs>}``.

        Raises:
            ValueError: ``path`` is a suite the package ships, a suite ``evals.yaml``
                names, or a file holding approved cases.
        """
        from nl2sql.evaluation.suite import check_target
        from nl2sql.feedback import draft_entries, write_drafts

        path = Path(path)
        folder = self._ctx.config.folder
        if not path.is_absolute() and folder is not None:
            path = Path(folder) / path
        check_target(path, [p if p.is_absolute() or folder is None else Path(folder) / p
                            for p in self._ctx.config.evals.suites])
        drafts, skipped = draft_entries(self.list())
        write_drafts(drafts, path)
        return {"written": len(drafts), "skipped": skipped}

    def clear(self) -> int:
        """Delete every rating; returns how many. Schema snapshots and the plan cache stay."""
        return self._ctx.state.clear_feedback()

    def _propose(self, row: Dict[str, Any]) -> None:
        """File a thumbs-up generated answer as a ``verified_query`` proposal (D43); other rows file nothing."""
        from nl2sql.config.curated import CuratedSqlError, inspect_verified_sql
        from nl2sql.feedback import skip_reason, verified_query
        from nl2sql.review.queue import propose, table_names
        from nl2sql_sdk.stores import Proposal

        if skip_reason(row):
            return
        datasource_id = row["datasource_ids"][0]
        if datasource_id not in self._ctx.ds_registry.list_ids():
            return
        change = verified_query(row)
        try:
            parsed = inspect_verified_sql(change["sql"], self._ctx.ds_registry.get_dialect(datasource_id))
        except CuratedSqlError:
            return  # not one read-only query: nothing a person could verify
        propose(self._ctx, Proposal.build(
            kind="verified_query", datasource_id=datasource_id, target=change["questions"][0], change=change,
            origin="thumbs_up", tables=table_names(self._ctx, datasource_id, parsed.tables),
            evidence={"trace_id": row["trace_id"], "role": row.get("role") or None}))

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
