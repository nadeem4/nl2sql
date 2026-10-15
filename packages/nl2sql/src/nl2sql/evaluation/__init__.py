"""Evals: suites of cases, scored by registered scorers, run through ``engine.evals``.

See docs/architecture/wave_a.md, section 8. ``suite`` is the case format,
``run`` the modes, ``scorers`` the interface and the built-ins, ``report`` the
scoring of a run, ``regenerate`` the expected rows and ``gold_llm`` the fake
LLM mode ``plans`` runs on. Nothing here is imported by the runtime at module
scope: answering a question never loads it.
"""
