"""``nl2sql profile``: the schema profiler (docs/architecture/wave_b.md, section 6).

P1 makes it run ``engine.knowledge.profile`` (``--datasource``, ``--as``) and
P2 adds ``--draft`` and ``--max-cost``.
"""
from __future__ import annotations

import typer

app = typer.Typer(help="Profile the indexed schema and file what it finds for review (from 0.8).",
                  no_args_is_help=True)
