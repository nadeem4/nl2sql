"""Answer feedback and guardrail rates over recorded runs.

* :mod:`record` -- what one run contributes (signals, question, SQL, models), and its rated row.
* :mod:`stats` -- rates over feedback rows plus kept run traces.
* :mod:`drafts` -- thumbs-up runs as draft eval cases for review.
* :mod:`proposals` -- thumbs-up generated answers as proposed verified queries for review.

The rows are kept by the engine's state store (``storage.state``).
"""
from nl2sql.feedback.drafts import draft_entries, write_drafts
from nl2sql.feedback.proposals import build_proposals, render_proposals, write_proposals
from nl2sql.feedback.record import NOTE_MAX_CHARS, RATINGS, REFUSAL_CODES, rated_row, run_record, run_signals
from nl2sql.feedback.stats import compute_stats, load_traces

__all__ = [
    "NOTE_MAX_CHARS", "RATINGS", "REFUSAL_CODES", "build_proposals", "compute_stats", "draft_entries",
    "load_traces", "rated_row", "render_proposals", "run_record", "run_signals", "write_drafts",
    "write_proposals",
]
