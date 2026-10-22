"""Answer feedback and guardrail rates over recorded runs.

* :mod:`record` -- what one run contributes (signals, question, SQL, models), and its rated row.
* :mod:`stats` -- rates over feedback rows plus kept run traces.
* :mod:`drafts` -- thumbs-up runs as draft eval cases for review.
* :mod:`proposals` -- a thumbs-up generated answer as a proposed verified query (the review queue's change).
* :mod:`corrections` -- a run's fix, checked as the corrector, filed as a proposed verified query.

The rows are kept by the engine's state store (``storage.state``).
"""
from nl2sql.feedback.drafts import draft_entries, write_drafts
from nl2sql.feedback.proposals import skip_reason, verified_query
from nl2sql.feedback.record import NOTE_MAX_CHARS, RATINGS, REFUSAL_CODES, rated_row, run_record, run_signals
from nl2sql.feedback.stats import compute_stats, load_traces

__all__ = [
    "NOTE_MAX_CHARS", "RATINGS", "REFUSAL_CODES", "compute_stats", "draft_entries", "load_traces", "rated_row",
    "run_record", "run_signals", "skip_reason", "verified_query", "write_drafts",
]
