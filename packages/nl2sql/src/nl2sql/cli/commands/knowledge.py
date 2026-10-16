"""``nl2sql knowledge``: the glossary as the engine uses it (docs/architecture/wave_b.md, section 3).

``show``, ``export`` and ``sources`` arrive with K1, each calling the matching
``engine.knowledge`` method with the caller built from ``--as`` and ``--user``.
"""
from __future__ import annotations

import typer

app = typer.Typer(help="The glossary as the engine uses it: `show`, `export`, `sources` (from 0.8).",
                  no_args_is_help=True)
