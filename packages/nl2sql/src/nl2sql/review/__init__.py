"""The review queue: proposals, and accept, the only writer of a person's file (docs/architecture/wave_b.md, section 5).

* :mod:`models` -- ``AcceptReport``, ``ReviewError`` and its codes.
* :mod:`queue` -- filing a proposal (identity, supersede), who sees one, who decides.
* :mod:`accept` -- accept and reject; the only caller of the two writers below.
* :mod:`curated_file` -- the curated section's own file and its canonical writer.
* :mod:`review_file` -- the review file, the queue's export and import format.

Nothing here is imported by ``import nl2sql``: ``engine.proposals`` imports it
inside each method.
"""
