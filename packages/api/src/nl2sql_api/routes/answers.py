"""The `answers` namespace of `/api/v1`: a person's saved answers.

Thin over ``engine.answers``: every handler passes the caller and calls one named method. A saved answer is its
owner's alone; another caller's is a 404, an admin included. Saving and running call no model.
"""
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Response

from nl2sql import NL2SQL, LLMOverride, QueryResult, SavedAnswer, SaveReport, UserContext
from nl2sql_api import identity
from nl2sql_api.dependencies import get_engine
from nl2sql_api.errors import ERROR_RESPONSE
from nl2sql_api.models.answers import RunAnswerBody, SaveBody
from nl2sql_api.overrides import llm_override

router = APIRouter(tags=["answers"])

Caller = Annotated[UserContext, Depends(identity.caller)]
Engine = Annotated[NL2SQL, Depends(get_engine)]

_MISSING = {404: {**ERROR_RESPONSE, "description": "No such saved answer (or one that is not the caller's): "
                                                   "ANSWER_NOT_FOUND."}, **identity.AUTH_RESPONSES}
_SAVE = {404: {**ERROR_RESPONSE, "description": "No such conversation for this caller."},
         409: {**ERROR_RESPONSE, "description": "The caller is at the cap of saved answers: ANSWER_LIMIT_REACHED."},
         422: {**ERROR_RESPONSE, "description": "The step did not answer, kept no plan, or is not the run named: "
                                                "NOT_SAVABLE."}, **identity.AUTH_RESPONSES}


@router.post("/answers", response_model=SaveReport, summary="Save an answer", responses=_SAVE)
def save_answer(body: SaveBody, user: Caller, engine: Engine):
    """Keeps the answer of an `ask` step of the caller's conversation and rates its run up. The plan comes from the
    stored step, never from the request. The reply says whether a proposal for the shared set was filed
    (`proposed`, `proposal_id`) or why not (`not_proposed_reason`). Saving the same run again returns the same
    answer."""
    return engine.answers.save(trace_id=body.trace_id, conversation_id=body.conversation_id, step=body.step,
                               note=body.note, user_context=user)


@router.get("/answers", response_model=List[SavedAnswer], summary="List saved answers",
            responses=identity.AUTH_RESPONSES)
def list_answers(user: Caller, engine: Engine, datasource_id: Optional[str] = None):
    """The caller's saved answers, optionally those of one datasource."""
    return engine.answers.list(user_context=user, datasource_id=datasource_id)


@router.get("/answers/{answer_id}", response_model=SavedAnswer, summary="Read a saved answer", responses=_MISSING)
def get_answer(answer_id: str, user: Caller, engine: Engine):
    """One saved answer, with its question and typed plan."""
    return engine.answers.get(answer_id, user_context=user)


@router.delete("/answers/{answer_id}", status_code=204, summary="Delete a saved answer", responses=_MISSING)
def delete_answer(answer_id: str, user: Caller, engine: Engine):
    """Deletes one saved answer."""
    engine.answers.delete(answer_id, user_context=user)
    return Response(status_code=204)


@router.post("/answers/{answer_id}/run", response_model=QueryResult, summary="Run a saved answer", responses=_MISSING)
def run_answer(answer_id: str, user: Caller, engine: Engine,
               override: Annotated[Optional[LLMOverride], Depends(llm_override)],
               body: Optional[RunAnswerBody] = None):
    """Runs the stored plan again as the caller: their data scope, hooks and approvals apply, and no model is
    called. `approval_ids` are request ids granted through `/approvals`. A plan the schema has outgrown comes back
    with status `error` and a first error coded `TILE_NEEDS_FIXING`."""
    approvals = (body.approval_ids if body else None) or None
    return engine.answers.run(answer_id, user_context=user, llm_override=override, approvals=approvals)
