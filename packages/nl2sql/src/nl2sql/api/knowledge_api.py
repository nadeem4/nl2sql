"""``engine.knowledge``: the glossary as the engine uses it, the profiler, and saving a clarification pick.

See docs/architecture/wave_b.md, sections 3, 6 and 7. Every method that shows
knowledge or schema takes the caller as a required keyword and shows only what
the caller's scope reaches (D60): there is no admin default. Each method has its
final signature; the Wave B PR named in its docstring builds it.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Union

if TYPE_CHECKING:
    from nl2sql.clarify.models import ClarificationChoice
    from nl2sql.knowledge.models import DatasourceKnowledgeView, KnowledgeSourceInfo
    from nl2sql.policies import UserContext
    from nl2sql.profiler.models import ProfileReport
    from nl2sql_sdk.stores import Proposal


def _not_built(pr: str) -> NotImplementedError:
    return NotImplementedError(f"Not built yet: Wave B's {pr} builds it (docs/architecture/wave_b.md, section 16).")


class KnowledgeAPI:
    """The engine's knowledge. Internal: reach it through ``NL2SQL.knowledge``."""

    def __init__(self, ctx: Any):
        self._ctx = ctx

    def sources(self, *, user_context: "UserContext") -> List["KnowledgeSourceInfo"]:
        """Each knowledge source: its type, item counts per datasource the caller reaches, sha256, when it
        loaded, and the last reload's error (K1)."""
        raise _not_built("K1")

    def show(self, datasource_id: Optional[str] = None, *,
             user_context: "UserContext") -> List["DatasourceKnowledgeView"]:
        """The merged knowledge as the engine uses it, each item with its origin and source, derived roles
        included, and the problems (names that do not resolve, joins whose types cannot be compared) (K1)."""
        raise _not_built("K1")

    def export(self, path: Optional[Union[str, os.PathLike]] = None, *, datasource_ids: Optional[Sequence[str]] = None,
               user_context: "UserContext") -> str:
        """The merged knowledge the caller may see as one canonical ``glossary.yaml``, returned and, with
        ``path``, written there; never over a config file or a knowledge source's own file (K1)."""
        raise _not_built("K1")

    def reload(self, *, user_context: "UserContext") -> List["KnowledgeSourceInfo"]:
        """Load every source again and swap atomically; needs an admin role. On a failure the previous
        knowledge stays live and the error is reported (K1)."""
        raise _not_built("K1")

    def profile(self, datasource_ids: Optional[Sequence[str]] = None, *, user_context: "UserContext",
                draft: bool = False, max_cost_usd: Optional[float] = None) -> List["ProfileReport"]:
        """Profile each datasource's live snapshot, probing only the tables the caller reaches, and file what
        it finds as proposals. ``draft=True`` also asks the ``profiler`` model for descriptions under
        ``max_cost_usd`` (default ``settings.profiler.max_cost_usd``) (P1; drafts, P2)."""
        raise _not_built("P1")

    def save_choice(self, choice: "ClarificationChoice", *, user_context: "UserContext",
                    term: Optional[str] = None) -> "Proposal":
        """Turn a clarification pick into a ``term`` proposal; it reaches the glossary only when an admin
        accepts it (L2)."""
        raise _not_built("L2")
