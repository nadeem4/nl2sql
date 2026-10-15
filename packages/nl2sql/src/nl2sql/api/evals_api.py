"""``engine.evals``: evaluation as a product.

A stub in the foundation PR, so the namespace exists for every track; V1
adds ``load``, ``run``, ``gate``, ``regenerate`` and ``scorers``, and V3
``seed``. It never imports ``nl2sql.evaluation`` at module scope. See
docs/architecture/wave_a.md, section 8, "The facade".
"""
from __future__ import annotations

from typing import Any


class EvalsAPI:
    """The engine's evals, configured by the ``evals`` section. Internal: reach it through ``NL2SQL.evals``."""

    def __init__(self, ctx: Any):
        self._ctx = ctx
