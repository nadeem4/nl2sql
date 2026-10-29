"""Accept and reject: the only code that writes a file a person owns (docs/architecture/wave_b.md, section 5, D42).

``accept`` writes two files at most: the first ``yaml`` knowledge source's
glossary (``table``, ``column``, ``join`` and ``term`` proposals) and the
``curated`` section's own file (``verified_query`` proposals). It groups the
accepted items by file and, for each file, takes the state store's lock
``accept:<real path>``, checks the file's hash once, applies every item, writes
once and reloads once. Every lock is taken and every file's hash is checked
before the first write, so a call refused by those checks writes nothing.

Each file's items are recorded as accepted (and ``proposal.decided``
published) right after that file is written, still under the locks, so a
second accept or a reject of the same id waits and then finds it decided. If
the second file's write then fails, the first file's items stay accepted and
the second file's stay proposed: the queue always matches the files.

An item that cannot be applied (its change does not validate, the curated
checks refuse its SQL, an id that is taken) is left proposed and reported in
``AcceptReport.refused``; the rest are written. After a glossary write the
knowledge is reloaded; after a curated write the runtime's section is swapped
(``Runtime.replace_curated``) and each datasource's curated entries are
refreshed in the index: ``rebuild_index(refresh=True)``, which runs
``IndexingOrchestrator.refresh``, so the next question can match it.
``reject`` takes the same locks, so it never decides an item an accept in
flight is writing.
"""
from __future__ import annotations

import os
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from nl2sql_sdk.stores import Proposal

from nl2sql.knowledge.yaml_source import write_glossary
from nl2sql.review.curated_file import curated_path, file_sha256, write_curated
from nl2sql.review.models import AcceptReport, ReviewError, ReviewErrorCode
from nl2sql.review.queue import who

GLOSSARY = "glossary"
CURATED = "curated"
LOCK_TTL_S = 60
LOCK_WAIT_S = 10.0  # how long an accept or reject waits for another of the same file


def _kind(proposal: Proposal) -> str:
    return CURATED if proposal.kind == "verified_query" else GLOSSARY


def accept(ctx: Any, ids: Sequence[str], by: Any, edits: Optional[Mapping[str, Mapping[str, Any]]] = None,
           *, replace: bool = False) -> AcceptReport:
    """Accept the proposals ``ids``. ``by`` is already checked as an admin.

    ``edits`` maps an id to a dict merged over its ``change``; with
    ``replace`` (a review file's ``apply``) the dict replaces the change.
    """
    report = AcceptReport()
    edits = dict(edits or {})
    groups: Dict[str, List[Proposal]] = {}
    for proposal_id in dict.fromkeys(ids):
        proposal = ctx.state.get_proposal(proposal_id)
        if proposal is None:
            report.refused[proposal_id] = "No such proposal."
        elif proposal.status != "proposed":
            report.refused[proposal_id] = f"It is {proposal.status}, not proposed."
        else:
            edit = edits.get(proposal_id)
            change = dict(edit) if replace and edit is not None else {**proposal.change, **dict(edit or {})}
            groups.setdefault(_kind(proposal), []).append(proposal.model_copy(update={"change": change}))
    if not groups:
        return report

    targets = {kind: _target(ctx, kind) for kind in groups}
    with ExitStack() as held:
        _lock_all(ctx, held, targets.values())
        # Read under the locks: another accept may just have decided an item, written and reloaded.
        for kind, items in groups.items():
            groups[kind] = [p for p in items if _still_proposed(ctx, p, report)]
        glossary_plan = _plan_glossary(ctx, groups[GLOSSARY], report) if groups.get(GLOSSARY) else None
        curated_plan = _plan_curated(ctx, groups[CURATED], report) if groups.get(CURATED) else None
        # Every file is checked before the first write, so a call refused here writes nothing.
        if glossary_plan and glossary_plan[1]:
            _unchanged(targets[GLOSSARY], glossary_plan[0].sha256)
        if curated_plan and curated_plan[1]:
            _unchanged(targets[CURATED], ctx.curated_sha256)
        if glossary_plan and glossary_plan[1]:
            _decide_written(ctx, report, by, *_write_glossary(ctx, *glossary_plan))
        if curated_plan and curated_plan[1]:
            _decide_written(ctx, report, by, *_write_curated(ctx, *curated_plan, report))

    if glossary_plan and glossary_plan[1]:
        report.warnings.extend(_shadowed(ctx, glossary_plan[1]))
    if curated_plan and curated_plan[1]:
        report.warnings.extend(_refreshed(ctx, sorted({p.datasource_id for p in curated_plan[1]})))
    return report


def _decide_written(ctx: Any, report: AcceptReport, by: Any, label: str, applied: List[Proposal]) -> None:
    """Record one written file's items as accepted, under its lock."""
    report.files_written.append(label)
    for proposal in applied:
        ctx.state.put_proposal(proposal)  # the change as accepted, edits included
        decided = ctx.state.decide_proposal(proposal.id, "accepted", by=who(by), wrote=label)
        report.accepted.append(decided)
        _published(ctx, decided, "accepted")


def reject(ctx: Any, reasons: Mapping[str, Optional[str]], by: Any) -> List[Proposal]:
    """Reject each id with its reason. Every id must be proposed, or nothing is rejected.

    Takes the locks of the files the ids would be accepted into, so an accept
    in flight finishes first.

    Raises:
        ReviewError: ``NOT_FOUND``, naming the ids that are missing or no longer proposed.
    """
    kinds = {_kind(row) for row in (ctx.state.get_proposal(i) for i in reasons) if row is not None}
    targets = []
    for kind in kinds:
        try:
            targets.append(_target(ctx, kind))
        except ReviewError:
            pass  # a file nothing can be accepted into: no accept to wait for
    with ExitStack() as held:
        _lock_all(ctx, held, targets)
        rows = {proposal_id: ctx.state.get_proposal(proposal_id) for proposal_id in reasons}
        missing = [i for i, row in rows.items() if row is None or row.status != "proposed"]
        if missing:
            raise ReviewError(ReviewErrorCode.NOT_FOUND, f"No proposed item with the id(s) {', '.join(missing)}.",
                              fix="List the queue (engine.proposals.list) for the open ids.")
        rejected = []
        for proposal_id, reason in reasons.items():
            decided = ctx.state.decide_proposal(proposal_id, "rejected", by=who(by), reason=reason)
            rejected.append(decided)
            _published(ctx, decided, "rejected")
    return rejected


def _lock_all(ctx: Any, held: ExitStack, paths) -> None:
    """Every ``accept:<real path>`` lock, in one order, so two callers never wait on each other in a circle."""
    for name in sorted({f"accept:{os.path.realpath(path)}" for path in paths}):
        held.enter_context(_locked(ctx, name))


# ---------------------------------------------------------------- where each kind is written


def _target(ctx: Any, kind: str) -> Path:
    """The file ``kind``'s items go to, or ``ReviewError(NOT_WRITABLE)`` with the fix."""
    if kind == GLOSSARY:
        source = _glossary_source(ctx)
        if source is None:
            raise ReviewError(ReviewErrorCode.NOT_WRITABLE,
                              "There is no yaml knowledge source to write the glossary into.",
                              fix="Add a `yaml` knowledge source (knowledge: [{type: yaml, path: glossary.yaml}]), "
                                  "or export the proposals and edit by hand.")
        return source.path
    config = ctx.config
    if config.curated is None:
        raise ReviewError(ReviewErrorCode.NOT_WRITABLE, "There is no curated section to add a verified query to.",
                          fix="Add `curated: {file: curated.yaml}` to nl2sql.yaml, then accept again.")
    path = curated_path(config)
    if path is None:
        raise ReviewError(ReviewErrorCode.NOT_WRITABLE,
                          "The curated section is written inline in nl2sql.yaml, or the config was built in code; "
                          "accept writes only the section's own file.",
                          fix="Move the section to its own file (`curated: {file: curated.yaml}`), or export the "
                              "proposals (engine.proposals.export) and edit by hand.")
    return path


def _glossary_source(ctx: Any):
    """The first ``yaml`` knowledge source as loaded, or None."""
    return next((s for s in ctx.knowledge.sources if s.type == "yaml" and s.path is not None), None)


@contextmanager
def _locked(ctx: Any, name: str) -> Iterator[None]:
    """The state store's lock on ``name``, waiting up to ``LOCK_WAIT_S`` for another accept to finish."""
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            lock = ctx.state.lock(name, ttl_s=LOCK_TTL_S)
            lock.__enter__()
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _still_proposed(ctx: Any, proposal: Proposal, report: AcceptReport) -> bool:
    row = ctx.state.get_proposal(proposal.id)
    if row is not None and row.status == "proposed":
        return True
    report.refused[proposal.id] = f"It is {row.status if row else 'gone'}, not proposed."
    return False


def _unchanged(path: Path, expected_sha256: Optional[str]) -> None:
    if file_sha256(Path(os.path.realpath(path))) != expected_sha256:
        raise ReviewError(ReviewErrorCode.FILE_CHANGED,
                          f"{Path(path).as_posix()} changed on disk since the engine loaded it.",
                          fix="Reload the engine (engine.knowledge.reload for the glossary; a new NL2SQL for "
                              "curated.yaml) and accept again.")


def _label(ctx: Any, path: Path) -> str:
    """``path`` relative to the folder of ``nl2sql.yaml`` when it is inside it, else as given."""
    folder = ctx.config.folder
    if folder is not None:
        try:
            return Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(folder))).as_posix()
        except ValueError:
            pass
    return Path(path).as_posix()


# ---------------------------------------------------------------- the glossary


def _plan_glossary(ctx: Any, proposals: List[Proposal], report: AcceptReport):
    from nl2sql.knowledge.apply import apply_proposal

    source = _glossary_source(ctx)
    limit = ctx.settings.knowledge.item_max_chars
    glossary, applied = source.glossary, []
    for proposal in proposals:
        try:
            glossary = apply_proposal(glossary, proposal, item_max_chars=limit)
        except ValueError as e:
            report.refused[proposal.id] = str(e)
            continue
        applied.append(proposal)
    return source, applied, glossary


def _write_glossary(ctx: Any, source, applied: List[Proposal], glossary) -> Tuple[str, List[Proposal]]:
    write_glossary(source.path, glossary, source.sha256)
    ctx.reload_knowledge()
    return _label(ctx, source.path), applied


def _shadowed(ctx: Any, applied: List[Proposal]) -> List[str]:
    """A warning for each accepted item an earlier knowledge source also sets: the earlier one wins."""
    from nl2sql.knowledge.apply import GLOSSARY_KINDS

    warnings = []
    for proposal in applied:
        if proposal.kind not in GLOSSARY_KINDS:
            continue
        for source in ctx.knowledge.sources:
            if source.type == "yaml" and source.path is not None:
                break  # the source just written: nothing before it shadows anything else
            if _sets(source.glossary.datasources.get(proposal.datasource_id), proposal):
                warnings.append(f"The knowledge source '{source.type}' comes before the glossary and also sets "
                                f"{proposal.kind} {proposal.target} in {proposal.datasource_id}; its value wins.")
    return warnings


def _sets(knowledge: Any, proposal: Proposal) -> bool:
    if knowledge is None:
        return False
    target = proposal.target.strip().lower()
    if proposal.kind in ("table", "column"):
        items = knowledge.tables if proposal.kind == "table" else knowledge.columns
        item = next((v for k, v in items.items() if k.strip().lower() == target), None)
        if item is None:
            return False
        fields = item.model_dump(exclude_unset=True)
        return any(fields.get(name) not in (None, [], {}) for name in proposal.change if name != "synonyms")
    if proposal.kind == "term":
        return any(t.term.strip().lower() == target for t in knowledge.terms)
    if proposal.kind == "example":
        return any(e.strip().lower() == target for e in knowledge.examples)

    def side(value):
        return ",".join(v.strip().lower() for v in ([value] if isinstance(value, str) else value))

    return any(f"{side(j.from_)}->{side(j.to)}" == target.replace(" ", "") for j in knowledge.joins)


# ---------------------------------------------------------------- curated.yaml


def _plan_curated(ctx: Any, proposals: List[Proposal], report: AcceptReport):
    from pydantic import ValidationError

    from nl2sql.config import Curated
    from nl2sql.config.curated import CuratedDatasource, VerifiedQuery, check_curated_queries

    dialects = {ds_id: ctx.ds_registry.get_dialect(ds_id) for ds_id in ctx.ds_registry.list_ids()}
    curated: Curated = ctx.curated
    applied = []
    for proposal in proposals:
        ds_id = proposal.datasource_id
        try:
            query = VerifiedQuery.model_validate(proposal.change)
        except ValidationError as e:
            report.refused[proposal.id] = "; ".join(error["msg"] for error in e.errors())
            continue
        section = curated.datasources.get(ds_id) or CuratedDatasource()
        if any(q.id == query.id for q in section.verified_queries):
            report.refused[proposal.id] = (f"{ds_id} already has a verified query with the id '{query.id}'; "
                                           "accept it with an edit that renames it.")
            continue
        candidate = Curated(datasources={**curated.datasources, ds_id: section.model_copy(
            update={"verified_queries": [*section.verified_queries, query]})})
        issues = [i for i in check_curated_queries(candidate, dialects).issues
                  if i.datasource_id == ds_id and i.query_id in (query.id, None)]
        if issues:
            report.refused[proposal.id] = "; ".join(i.message for i in issues)
            continue
        curated, applied = candidate, [*applied, proposal]
    return curated, applied


def _write_curated(ctx: Any, curated, applied: List[Proposal], report: AcceptReport) -> Tuple[str, List[Proposal]]:
    path = curated_path(ctx.config)
    sha256 = write_curated(path, curated, ctx.curated_sha256)
    ctx.replace_curated(curated, sha256=sha256)
    return _label(ctx, path), applied


def _refreshed(ctx: Any, datasource_ids: List[str]) -> List[str]:
    """Refresh each datasource's curated index entries; a warning for each that could not be."""
    from nl2sql.indexing.rebuild import rebuild_index

    try:
        result = rebuild_index(ctx, datasource_ids=datasource_ids, refresh=True)
    except Exception as e:  # the file is written either way; the index catches up on the next refresh
        return [f"The index was not refreshed: {e}. Run `nl2sql index --refresh`."]
    return [f"The index of {error['datasource_id']} was not refreshed: {error['error']} "
            f"Run `nl2sql index --refresh` once it is indexed." for error in result.errors]


def _published(ctx: Any, proposal: Proposal, decision: str) -> None:
    ctx.bus.publish("proposal.decided", {"id": proposal.id, "kind": proposal.kind,
                                         "datasource_id": proposal.datasource_id, "decision": decision,
                                         "by": proposal.decided_by, "wrote": proposal.wrote},
                    tenant_id=ctx.tenant_id)


