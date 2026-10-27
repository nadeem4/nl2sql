"""Asking again: the checks on a ``ClarificationChoice`` and the pin it becomes (wave_b.md, section 7; D49).

The re-ask is stateless. ``choice_problem`` is the consistency check (the id is an unkeyed hash: a guard against
mistakes and mixed-up requests, not tamper evidence; the validator, run on the chosen plan with the caller's
scope, is the guard against an edited plan). ``pinned_from_choice`` is the run it pins: Wave A's pinned entry
with one sub-query, standalone, and the chosen option's plan as ``plan_source="chosen"``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from nl2sql.clarify.models import ClarificationChoice
from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery
from nl2sql.pipeline.pinned import PinnedRun, PinnedSource
from nl2sql_sdk.policy import UserContext
from nl2sql_sdk.stores import Proposal

NODE = "clarification_choice"
STALE = "The data or the glossary changed since the options were offered. Ask again."


def _invalid(message: str) -> PipelineError:
    return PipelineError(node=NODE, message=message, severity=ErrorSeverity.CRITICAL,
                         error_code=ErrorCode.CLARIFICATION_INVALID)


def _stale(message: str = STALE) -> PipelineError:
    return PipelineError(node=NODE, message=message, severity=ErrorSeverity.ERROR,
                         error_code=ErrorCode.CLARIFICATION_STALE)


def choice_problem(choice: ClarificationChoice, *, question: str, user: Optional[UserContext], ctx: Any,
                   approvals: Sequence[Any] = ()) -> Optional[PipelineError]:
    """Why ``choice`` cannot run for this caller and question, or None when it can.

    ``question`` is the question as passed to ``run_query``, before any ``run.before`` hook. ``ctx`` is the
    runtime, for the live schema version, knowledge digest and the caller's scope.
    """
    request, user = choice.request, user or UserContext()
    if approvals:
        return _invalid("A choice cannot come with approvals: ask again with one or the other.")
    if request.id != request.expected_id():
        return _invalid("The clarification request was changed or is not one this engine asked.")
    if request.question != question or (request.asker_user_id, request.asker_tenant_id) != (user.user_id,
                                                                                            user.tenant_id):
        return _invalid("The clarification request is for another question or another asker.")
    if choice.option_id not in {option.id for option in request.options}:
        return _invalid(f"The request has no option '{choice.option_id}'.")
    ds_id = request.datasource_id
    try:
        sub_query = SubQuery.model_validate(request.sub_query)
        for option in request.options:
            PlanModel.model_validate(option.plan)
    except ValueError:
        return _invalid("The clarification request holds a sub-query or a plan that is not valid.")
    if sub_query.datasource_id != ds_id:
        return _invalid("The clarification request's sub-query is for another datasource.")
    if ds_id not in ctx.ds_registry.list_ids() or not ctx.policy_engine.scope(user, ds_id).reaches_datasource():
        return _stale(f"The datasource '{ds_id}' is no longer one this caller may read. Ask again.")
    if (ctx.vector_store.schema_version(ds_id) or "") != request.schema_version or (
            ctx.knowledge.digest(ds_id) != request.knowledge_digest):
        return _stale()
    return None


def pinned_from_choice(choice: ClarificationChoice) -> PinnedRun:
    """The run ``choice`` pins: its sub-query, standalone, with the chosen option's plan."""
    sub_query = SubQuery.model_validate(choice.request.sub_query)
    option = next(o for o in choice.request.options if o.id == choice.option_id)
    decomposition = DecomposerResponse(
        sub_queries=[sub_query],
        combine_groups=[CombineGroup(group_id="chosen", operation="standalone",
                                     inputs=[CombineInput(subquery_id=sub_query.id)])])
    return PinnedRun(source=PinnedSource.CHOSEN, decomposition=decomposition, chosen_label=option.label,
                     plans={sub_query.id: PlanModel.model_validate(option.plan)})


def _qualified(table: Any) -> str:
    return f"{table.schema_name}.{table.name}" if table.schema_name else table.name


def _select_columns(plan: PlanModel) -> List[str]:
    """The ``table.column`` names the plan's select items read, in order, each once."""
    aliases = {t.alias: t for t in plan.tables}
    found: Dict[str, None] = {}

    def walk(expr: Any) -> None:
        if expr is None:
            return
        if expr.kind == "column" and expr.column_name:
            table = aliases.get(expr.alias) or (plan.tables[0] if len(plan.tables) == 1 else None)
            if table is not None:
                found.setdefault(f"{_qualified(table)}.{expr.column_name}")
        for child in [expr.left, expr.right, expr.expr, expr.else_expr, *expr.args]:
            walk(child)
        for when in expr.whens:
            walk(when.condition)
            walk(when.result)

    for item in plan.select_items:
        walk(item.expr)
    return list(found)


def choice_proposal(ctx: Any, choice: ClarificationChoice, user: UserContext, term: Optional[str] = None) -> Proposal:
    """The ``term`` proposal that saving ``choice`` files (D50): the phrase (or ``term``) named, the option's
    label as its definition, the columns the chosen plan's select items read, and the tables it reads.

    A ``term`` other than the phrase also lists the phrase as a synonym, so the phrase is settled once accepted.

    Raises:
        ValueError: the request was changed, or has no such option.
    """
    from nl2sql.review.queue import table_names

    request = choice.request
    option = next((o for o in request.options if o.id == choice.option_id), None)
    if request.id != request.expected_id() or option is None:
        raise ValueError("The clarification request was changed, or has no such option.")
    plan = PlanModel.model_validate(option.plan)
    name = (term or request.phrase).strip()
    change: Dict[str, Any] = {"term": name, "definition": option.label,
                              "columns": _select_columns(plan), "tables": sorted({_qualified(t) for t in plan.tables})}
    if name.casefold() != request.phrase.strip().casefold():
        change["synonyms"] = [request.phrase]
    tables = table_names(ctx, request.datasource_id, [(t.schema_name or "", t.name) for t in plan.tables])
    return Proposal.build(kind="term", datasource_id=request.datasource_id, target=name, change=change,
                          origin="clarification", tables=tables, schema_version=request.schema_version,
                          created_by=user.user_id, evidence={"option": option.id})
