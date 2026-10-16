"""``nl2sql review``: the review queue (docs/architecture/wave_b.md, section 5).

``list``, ``show``, ``accept``, ``reject``, ``export`` and ``apply`` arrive with
Q1, each calling the matching ``engine.proposals`` method.
"""
from __future__ import annotations

import typer

app = typer.Typer(help="The review queue: `list`, `show`, `accept`, `reject`, `export`, `apply` (from 0.8).",
                  no_args_is_help=True)
