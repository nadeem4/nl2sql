"""``engine.traces``: replaying a recorded run."""

from __future__ import annotations

from typing import Any, Dict

from nl2sql.runtime import Runtime


class TracesAPI:
    """``engine.traces``. Internal: reach it through ``NL2SQL``."""

    def __init__(self, ctx: Runtime):
        self._ctx = ctx

    def replay(self, trace: Dict[str, Any]):
        """Re-run a traced question, answering every LLM call from the recording. No model is called.

        ``trace`` is a trace document, as written under ``<storage.path>/traces``.
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
