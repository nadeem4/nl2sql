"""``LLMOverride``: one call's own keys and models (docs/architecture/wave_d.md, D210)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Tuple


@dataclass(frozen=True)
class LLMOverride:
    """What a caller brings for one call, in place of the configured models.

    ``keys`` maps a provider to the caller's key for it; ``models`` maps an agent name to ``(provider, model)``.
    A key is never printed: it is left out of ``repr``. Wiring it into the run is G2a.
    """

    keys: Mapping[str, str] = field(default_factory=dict, repr=False)
    models: Mapping[str, Tuple[str, str]] = field(default_factory=dict)
