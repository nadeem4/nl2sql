"""Knowledge: the glossary as the engine uses it (docs/architecture/wave_b.md, sections 3 and 4).

The glossary's models and the ``KnowledgeSource`` interface are in the SDK
(``nl2sql_sdk.knowledge``). ``models`` holds the shapes the engine's tracks
share; the sources, merging, resolution and rendering arrive with K1 and K2.
Nothing here is imported by ``import nl2sql``.
"""
