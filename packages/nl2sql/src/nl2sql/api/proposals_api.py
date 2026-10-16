"""``engine.proposals``: the review queue (docs/architecture/wave_b.md, section 5).

Reading the queue takes the caller (D60): a caller sees a proposal only when
their scope reaches its datasource and every table it names, and ``get`` of one
they cannot see is "not found". Deciding needs ``by`` with an admin role
(``policies.admin_roles``; under ``allow_all``, any caller); accept is the only
code that writes a person's file (D42). ``nl2sql.review`` is imported inside
each method, so answering a question never loads it.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Set, Union

if TYPE_CHECKING:
    from nl2sql.policies import UserContext
    from nl2sql.review.models import AcceptReport
    from nl2sql_sdk.stores import Proposal

PathLike = Union[str, "os.PathLike[str]"]


def _ids(ids: Union[str, Sequence[str]]) -> List[str]:
    return [ids] if isinstance(ids, str) else list(ids)


class ProposalsAPI:
    """The review queue. Internal: reach it through ``NL2SQL.proposals``."""

    def __init__(self, ctx: Any):
        self._ctx = ctx

    def list(self, *, user_context: "UserContext", kind: Optional[str] = None, status: Optional[str] = "proposed",
             datasource_id: Optional[str] = None, limit: Optional[int] = None) -> List["Proposal"]:
        """The proposals the caller may see, newest first. ``status=None`` lists every status.

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``.
        """
        from nl2sql.review.queue import caller, visible

        who = caller(user_context)
        rows = self._ctx.state.list_proposals(kind=kind, status=status, datasource_id=datasource_id)
        seen = [p for p in rows if visible(self._ctx, who, p)]
        return seen[:limit] if limit is not None else seen

    def get(self, id: str, *, user_context: "UserContext") -> "Proposal":
        """One proposal. One the caller's scope cannot see is not found (``ReviewError``, ``NOT_FOUND``)."""
        from nl2sql.review.models import ReviewError, ReviewErrorCode
        from nl2sql.review.queue import caller, visible

        who = caller(user_context)
        proposal = self._ctx.state.get_proposal(id)
        if proposal is None or not visible(self._ctx, who, proposal):
            raise ReviewError(ReviewErrorCode.NOT_FOUND, f"No proposal with the id {id!r}.",
                              fix="List the queue (engine.proposals.list) for the ids you can see.")
        return proposal

    def current(self, id: str, *, user_context: "UserContext") -> Optional[Dict[str, Any]]:
        """What the proposal would replace, beside its ``change``: the glossary item as the caller sees it, or
        the curated query with the same id. None when there is none, or the caller may not see it (a curated
        query reading a table outside their scope).

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``.
            ReviewError: ``NOT_FOUND``, as ``get``.
        """
        from nl2sql.review.queue import caller, current_value

        who = caller(user_context)
        return current_value(self._ctx, who, self.get(id, user_context=who))

    def accept(self, ids: Union[str, Sequence[str]], *, by: "UserContext",
               edits: Optional[Dict[str, Dict[str, Any]]] = None) -> "AcceptReport":
        """Accept one id or a list, writing each file once. ``edits`` maps an id to a dict merged over its
        ``change``, so a reviewer can fix a description or rename a verified query.

        An id that is unknown or no longer proposed, or whose change does not
        pass the checks (the glossary's validation; the curated checks for a
        verified query), is left as it is and named in ``refused``.

        Raises:
            TypeError: ``by`` is not a ``UserContext``.
            ReviewError: ``NOT_AUTHORIZED`` (no admin role), ``NOT_WRITABLE``
                (no ``yaml`` knowledge source; a ``curated`` section that is
                inline, built in code or absent) or ``FILE_CHANGED`` (a file
                changed on disk since the engine loaded it). Nothing is written.
        """
        from nl2sql.review.accept import accept
        from nl2sql.review.queue import require_admin

        return accept(self._ctx, _ids(ids), require_admin(self._ctx, by), edits)

    def reject(self, ids: Union[str, Sequence[str]], *, by: "UserContext",
               reason: Optional[str] = None) -> List["Proposal"]:
        """Reject one id or a list; a rejected change is never proposed again.

        Raises:
            TypeError: ``by`` is not a ``UserContext``.
            ReviewError: ``NOT_AUTHORIZED``, or ``NOT_FOUND`` when an id is not
                proposed (then nothing is rejected).
        """
        from nl2sql.review.accept import reject
        from nl2sql.review.queue import require_admin

        by = require_admin(self._ctx, by)
        return reject(self._ctx, {proposal_id: reason for proposal_id in _ids(ids)}, by)

    def export(self, path: PathLike, *, user_context: "UserContext", kind: Optional[str] = None,
               datasource_id: Optional[str] = None) -> int:
        """Write the proposed items the caller can see as a review file; returns how many.

        Raises:
            TypeError: ``user_context`` is not a ``UserContext``.
            ValueError: ``path`` is ``nl2sql.yaml``, one of its section files or a knowledge source's file.
        """
        from nl2sql.review.review_file import render

        target = Path(path)
        if Path(os.path.realpath(target)) in self._protected_files():
            raise ValueError(f"{target.as_posix()} is a file of the config or a knowledge source; a review file "
                             "never goes there. Pick another path.")
        proposals = self.list(user_context=user_context, kind=kind, datasource_id=datasource_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(proposals), encoding="utf-8", newline="\n")
        return len(proposals)

    def apply(self, path: PathLike, *, by: "UserContext") -> "AcceptReport":
        """Read a review file: accept the items marked ``accept`` (their ``change`` in the file replaces the
        stored one, so a reviewer can remove a key) and reject those marked ``reject`` (with their ``reason``).

        Items left empty stay proposed. An id that is unknown or no longer
        proposed is named in ``refused``; the rejected ones are in ``rejected``.

        Raises:
            TypeError, ReviewError: as ``accept``.
            ValueError: the file is not a review file.
        """
        from nl2sql.review.accept import accept, reject
        from nl2sql.review.queue import require_admin
        from nl2sql.review.review_file import read

        by = require_admin(self._ctx, by)
        document = read(Path(path))
        state = self._ctx.state
        to_accept, edits, reasons, refused = [], {}, {}, {}
        for item in document.proposals:
            if item.decision is None:
                continue
            stored = state.get_proposal(item.id)
            if stored is None:
                refused[item.id] = "No such proposal."
            elif stored.status != "proposed":
                refused[item.id] = f"It is {stored.status}, not proposed."
            elif item.decision == "accept":
                to_accept.append(item.id)
                if item.change is not None and item.change != stored.change:
                    edits[item.id] = item.change
            else:
                reasons[item.id] = item.reason
        report = accept(self._ctx, to_accept, by, edits, replace=True)
        report.refused.update(refused)
        report.rejected = reject(self._ctx, reasons, by) if reasons else []
        return report

    def _protected_files(self) -> Set[Path]:
        """The config's own files and each knowledge source's file, as real paths."""
        config = self._ctx.config
        files = [source.path for source in self._ctx.knowledge.sources if source.path is not None]
        if config.folder is not None:
            files.append(config.folder / "nl2sql.yaml")
            for section in type(config).model_fields:
                file, _ = config.locate(section)
                if file != "nl2sql.yaml":
                    files.append(config.folder / file)
            for i in range(len(config.datasources)):
                files.append(config.folder / config.locate(f"datasources[{i}]")[0])
        return {Path(os.path.realpath(f)) for f in files}
