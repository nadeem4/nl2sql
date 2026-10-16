"""Why this answer: the run's plan in plain English, with no model call (docs/architecture/wave_b.md, section 8).

``models`` holds the shapes ``QueryResult.explanation`` carries; the renderer
arrives with X1 and is imported only where it is used.
"""
