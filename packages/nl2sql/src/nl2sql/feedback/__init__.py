"""Answer feedback and guardrail rates over recorded runs.

* :mod:`record` -- what one run contributes (signals, question, SQL, models).
* :mod:`store` -- the ``feedback`` table in the SQLite schema store.
* :mod:`stats` -- rates over feedback rows plus kept run traces.
* :mod:`drafts` -- thumbs-up runs as draft gold entries for review.
"""
from nl2sql.feedback.drafts import draft_entries, write_drafts
from nl2sql.feedback.record import REFUSAL_CODES, run_record, run_signals
from nl2sql.feedback.stats import compute_stats, load_traces
from nl2sql.feedback.store import NOTE_MAX_CHARS, RATINGS, FeedbackStore

__all__ = [
    "FeedbackStore", "NOTE_MAX_CHARS", "RATINGS", "REFUSAL_CODES", "compute_stats", "draft_entries",
    "load_traces", "run_record", "run_signals", "write_drafts",
]
