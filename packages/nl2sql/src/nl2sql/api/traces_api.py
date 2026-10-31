"""``engine.traces``: reading and replaying recorded runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from nl2sql.runtime import Runtime

if TYPE_CHECKING:
    from nl2sql.policies import UserContext


class TracesAPI:
    """``engine.traces``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def get(self, trace_id: str, *, user_context: Optional["UserContext"] = None) -> Optional[Dict[str, Any]]:
        """One recorded run's trace document, by its ``trace_id``, or None when none was kept.

        With a ``user_context`` only the caller who asked the run reads it: a run another
        user (or the same user id in another tenant) asked, and a trace that records no
        asker, are None, as a missing one. Without one, the caller is the trusted
        in-process operator and reads any run (``nl2sql trace``); the REST API, MCP and
        the terminal app always pass the caller.

        Read from the trace store (``storage.traces``; the ``files`` store
        reads ``<storage.path>/traces``). The id must be a plain token
        (letters, digits, ``-`` and ``_``), so no id can name a file elsewhere.
        Which runs are kept is ``settings.traces.mode``, and for how long the
        store's ``max_age_days``.

        Raises:
            ValueError: ``trace_id`` is not a plain token, or the file is not a trace.
            TypeError: ``user_context`` is given and is not a ``UserContext``.
        """
        from nl2sql.execution.artifacts.results import ResultOwner
        from nl2sql.policies import UserContext
        from nl2sql.tracing.document import validate_trace_id

        if user_context is not None and not isinstance(user_context, UserContext):
            raise TypeError("user_context must be a UserContext, or None for the in-process operator.")
        doc = self._ctx.traces.get(validate_trace_id(trace_id))
        if doc is None or user_context is None:
            return doc
        # The owner is worked out exactly as the writer works it out (``ResultOwner.of``).
        asker = ResultOwner.of(user_context, getattr(self._ctx, "tenant_id", None)).model_dump()
        return doc if doc.get("owner") == asker else None

    def replay(self, trace: Dict[str, Any]):
        """Re-run a traced question, answering every LLM call from the recording. No model is called.

        ``trace`` is a trace document, as ``get`` returns it.
        The replay runs the real pipeline against the same datasources, on a
        runtime of its own, so the engine's config and LLMs never change.

        Returns:
            A ``ReplayReport``: the replayed ``result``, how many recorded calls
            were ``served``, the ``divergence`` (where the replay asked for a
            call the recording does not have) and the ``differences`` from the
            recorded result.

        Raises:
            LookupError: a datasource the trace used is not configured here or
                does not answer; the message names each one and why.
        """
        from nl2sql.tracing.replay import replay_trace, unreachable_datasources

        problems = unreachable_datasources(trace, self._ctx)
        if problems:
            raise LookupError("\n".join(problems))
        return replay_trace(trace, self._ctx)
