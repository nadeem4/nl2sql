"""``engine.proposals``: the review queue (docs/architecture/wave_b.md, section 5).

Reading the queue takes the caller (D60): a caller sees a proposal only when
their scope reaches every table it names, and ``get`` of one they cannot see is
"not found". Deciding needs ``by`` with an admin role (``policies.admin_roles``);
accept is the only code that writes a person's file (D42). Each method has its
final signature; Q1 builds them.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Union

if TYPE_CHECKING:
    from nl2sql.policies import UserContext
    from nl2sql.review.models import AcceptReport
    from nl2sql_sdk.stores import Proposal

PathLike = Union[str, "os.PathLike[str]"]


def _not_built() -> NotImplementedError:
    return NotImplementedError("Not built yet: Wave B's Q1 builds it (docs/architecture/wave_b.md, section 16).")


class ProposalsAPI:
    """The review queue. Internal: reach it through ``NL2SQL.proposals``."""

    def __init__(self, ctx: Any):
        self._ctx = ctx

    def list(self, *, user_context: "UserContext", kind: Optional[str] = None, status: Optional[str] = "proposed",
             datasource_id: Optional[str] = None, limit: Optional[int] = None) -> List["Proposal"]:
        """The proposals the caller may see, newest first."""
        raise _not_built()

    def get(self, id: str, *, user_context: "UserContext") -> "Proposal":
        """One proposal. One the caller's scope cannot see is not found (``ReviewError``, ``NOT_FOUND``)."""
        raise _not_built()

    def accept(self, ids: Union[str, Sequence[str]], *, by: "UserContext",
               edits: Optional[Dict[str, Dict[str, Any]]] = None) -> "AcceptReport":
        """Accept one id or a list, writing each file once. ``edits`` maps an id to a dict merged over its
        ``change``."""
        raise _not_built()

    def reject(self, ids: Union[str, Sequence[str]], *, by: "UserContext",
               reason: Optional[str] = None) -> List["Proposal"]:
        """Reject one id or a list; a rejected change is never proposed again."""
        raise _not_built()

    def export(self, path: PathLike, *, user_context: "UserContext", kind: Optional[str] = None,
               datasource_id: Optional[str] = None) -> int:
        """Write the proposed items the caller can see as a review file; returns how many."""
        raise _not_built()

    def apply(self, path: PathLike, *, by: "UserContext") -> "AcceptReport":
        """Read a review file: accept the items marked ``accept`` (with their edited ``change``) and reject
        those marked ``reject``."""
        raise _not_built()
