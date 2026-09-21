"""Run traces: record every node of a run, write it as one file, and replay it offline.

* :mod:`nl2sql.tracing.recorder` -- the callback that records a run.
* :mod:`nl2sql.tracing.trace` -- assembles the document and writes it per ``TRACE_MODE``.
* :mod:`nl2sql.tracing.document` -- JSON conversion, redaction, capping, file naming.
* :mod:`nl2sql.tracing.replay` -- re-runs a trace feeding back the recorded LLM responses.
"""
