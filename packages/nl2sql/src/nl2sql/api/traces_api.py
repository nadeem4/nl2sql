"""``engine.traces``: reading and replaying recorded runs."""

from __future__ import annotations

from typing import Any, Dict, Optional

from nl2sql.runtime import Runtime


class TracesAPI:
    """``engine.traces``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def get(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """One recorded run's trace document, by its ``trace_id``, or None when none was kept.

        Read from the trace store (``storage.traces``; the ``files`` store
        reads ``<storage.path>/traces``). The id must be a plain token
        (letters, digits, ``-`` and ``_``), so no id can name a file elsewhere.
        Which runs are kept is ``settings.traces.mode``, and for how long the
        store's ``max_age_days``.

        Raises:
            ValueError: ``trace_id`` is not a plain token, or the file is not a trace.
        """
        from nl2sql.tracing.document import validate_trace_id

        return self._ctx.traces.get(validate_trace_id(trace_id))

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
