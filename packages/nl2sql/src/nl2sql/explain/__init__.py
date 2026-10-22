"""Why this answer: the run's plan in plain English, with no model call (docs/architecture/wave_b.md, section 8).

``models`` holds the shapes ``QueryResult.explanation`` carries; ``render``
(with ``phrases``) builds them and is imported only where it is used, when a
run's result is assembled, so ``import nl2sql`` never loads it.
"""
