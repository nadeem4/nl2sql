"""The `knowledge` namespace of `/api/v1` (docs/architecture/wave_d.md, "The routes", row A3): what the engine knows
about each datasource, and the proposals that change it. Thin over ``engine.knowledge`` and ``engine.proposals``.
Profiling is the `profile` job (`POST /jobs/profile`, `routes/jobs.py`). Everything about proposals is admin work:
a proposal can name a table the caller's scope hides, and accepting one writes a file."""
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Query

from nl2sql import (AcceptReport, DatasourceKnowledgeView, KnowledgeSourceInfo, NL2SQL, Proposal, UserContext)
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE
from nl2sql_api.models.knowledge import AcceptRequest, ChoiceRequest, ExamplesRequest, RejectRequest

router = APIRouter(tags=["knowledge"])

Engine = Annotated[NL2SQL, Depends(get_engine)]
Caller = Annotated[UserContext, Depends(identity.caller)]
Admin = Annotated[UserContext, Depends(identity.admin)]

_UNKNOWN = {404: {**ERROR_RESPONSE, "description": "A named datasource is not configured, or the caller cannot "
                                                     "reach it."}}
_REVIEW = {404: {**ERROR_RESPONSE, "description": "No such proposal, or one the caller's scope cannot see "
                                                    "(`NOT_FOUND`)."},
           403: {**ERROR_RESPONSE, "description": "Not an admin (`NOT_AUTHORIZED`), or the config has no writable "
                                                    "knowledge file (`NOT_WRITABLE`)."},
           409: {**ERROR_RESPONSE, "description": "The file changed on disk since the engine loaded it "
                                                    "(`FILE_CHANGED`): reload and try again."}}


@router.get("/knowledge", response_model=List[DatasourceKnowledgeView], summary="Show the knowledge",
            responses={**_UNKNOWN, **identity.AUTH_RESPONSES})
def show_knowledge(engine: Engine, user: Caller, datasource_id: Optional[str] = None):
    """The merged knowledge the engine uses for each datasource the caller may see, or for `datasource_id`: each
    item with where it came from, and the `problems` (names that do not resolve against the live schema, joins whose
    types cannot be compared)."""
    return engine.knowledge.show(datasource_id, user_context=user)


@router.get("/knowledge/sources", response_model=List[KnowledgeSourceInfo], summary="List knowledge sources",
            responses=identity.AUTH_RESPONSES)
def knowledge_sources(engine: Engine, user: Caller):
    """Each knowledge source (`yaml`, ...): what it holds per datasource the caller reaches, its checksum, when it
    loaded and the last reload's error."""
    return engine.knowledge.sources(user_context=user)


@router.post("/knowledge/reload", response_model=List[KnowledgeSourceInfo], summary="Reload the knowledge",
             responses=identity.ADMIN_RESPONSES)
def reload_knowledge(engine: Engine, user: Admin):
    """Load every source again and swap atomically; a failing source keeps its previous knowledge and says why in
    `error`. Runs in flight keep what they started with. Admin only."""
    return engine.knowledge.reload(user_context=user)


@router.post("/knowledge/choices", response_model=Proposal, summary="Save a clarification choice",
             responses=identity.AUTH_RESPONSES)
def save_choice(body: ChoiceRequest, engine: Engine, user: Caller):
    """File the caller's clarification pick as a `term` proposal, so the next person is not asked. `term` names
    the phrase when it differs from the one the engine asked about. It changes nothing until an admin accepts it."""
    return engine.knowledge.save_choice(body.choice, user_context=user, term=body.term)


@router.get("/proposals", response_model=List[Proposal], summary="List proposals",
            responses=identity.ADMIN_RESPONSES)
def list_proposals(engine: Engine, user: Admin, kind: Optional[str] = None, status: Optional[str] = "proposed",
                   datasource_id: Optional[str] = None, limit: Annotated[Optional[int], Query(ge=1, le=500)] = None):
    """Proposed knowledge changes, newest first: `kind` (`term`, `join`, `description`, `example`, ...), `status`
    (`proposed` by default), `datasource_id`. Admin only."""
    return engine.proposals.list(user_context=user, kind=kind, status=status, datasource_id=datasource_id,
                                 limit=limit)


@router.get("/proposals/{proposal_id}", response_model=Proposal, summary="Read a proposal",
            responses={**identity.ADMIN_RESPONSES, **_REVIEW})
def get_proposal(proposal_id: str, engine: Engine, user: Admin):
    """One proposal. Admin only."""
    return engine.proposals.get(proposal_id, user_context=user)


@router.post("/proposals/accept", response_model=AcceptReport, summary="Accept proposals",
             responses={**identity.ADMIN_RESPONSES, **_REVIEW})
def accept_proposals(body: AcceptRequest, engine: Engine, user: Admin):
    """Accept the proposals in `ids`, writing each file once. `edits` maps an id to the fields merged over its
    change first (a fixed description, a renamed query). The report lists what was `accepted`, the `files_written`,
    `warnings` and, per id, why one was `refused`. Admin only."""
    return engine.proposals.accept(body.ids, by=user, edits=body.edits)


@router.post("/proposals/reject", response_model=List[Proposal], summary="Reject proposals",
             responses={**identity.ADMIN_RESPONSES, **_REVIEW})
def reject_proposals(body: RejectRequest, engine: Engine, user: Admin):
    """Reject the proposals in `ids`; a rejected change is never proposed again. Admin only."""
    return engine.proposals.reject(body.ids, by=user, reason=body.reason)


@router.post("/proposals/examples", response_model=List[Proposal], summary="File starter questions",
             responses={**identity.ADMIN_RESPONSES, **_REVIEW})
def propose_examples(body: ExamplesRequest, engine: Engine, user: Admin):
    """File each question as an `example` proposal for `datasource_id`: a question the datasource can answer,
    which an accept appends to the glossary's `examples`. Nothing reaches routing until an admin accepts. Admin
    only."""
    return engine.proposals.propose_examples(body.datasource_id, body.questions, by=user)
