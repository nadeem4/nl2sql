"""Knowledge: the glossary as the engine uses it (docs/architecture/wave_b.md, sections 3 and 4).

The glossary's models and the ``KnowledgeSource`` interface are in the SDK
(``nl2sql_sdk.knowledge``). Here:

- ``models``: the shapes the engine's tracks share (``KnowledgeUsed``,
  ``KnowledgeSourceInfo``, ``DatasourceKnowledgeView``);
- ``yaml_source``: the built-in ``yaml`` source and the engine-internal
  glossary writer (``write_glossary``, ``canonical_yaml``);
- ``merge``: loading the sources and merging them in config order
  (``Knowledge``, its digests and provenance);
- ``resolve``: names against the live snapshot, derived roles and problems;
- ``scope``: the caller's view, by the names an item lists and the denied
  tables its text mentions;
- ``apply``: a proposal's change merged into a glossary, for the review
  queue's accept.

Rendering for the prompts arrives with K2. Nothing here is imported by
``import nl2sql``.
"""
