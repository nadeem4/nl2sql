"""The clarifier: ask instead of guess, when the planner flagged a phrase it could read two ways (wave_b.md, section 7).

It sits on the logical validator's "valid" edge, before the generator, so
every path from the planner to the generator still passes the validator. With
no alternatives it passes straight through.

Each alternative reached it through ``plan.after`` in the planner node (one a
hook denied is not here; one a hook changed is here as changed). The clarifier
runs each through the validator's own checks (``LogicalValidatorNode.check``:
the static checks and the caller's scope) and drops any that fail, then
renders each that passed and drops one whose SQL another option already has.
An option is never offered, nor listed as an assumption, unless the validator
would pass it, so no option names a table the caller cannot read.

When at least one alternative is left, the planner named the phrase, and the
sub-query may ask (``state.clarify``: the run's ``clarify`` is on and this is
its only sub-query, D47) and ``settings.clarify.enabled`` is on, the sub-query
ends with ``NEEDS_CLARIFICATION`` (INFO, never retried) before the generator,
and ``clarification.requested`` is published. The error's ``details`` carry
what the result needs to build the ``ClarificationRequest``
(``api/query_api.py``): the options' labels and plans, the phrase, the
sub-query, its schema version and the datasource's knowledge digest.
Otherwise the default plan goes on to the generator, and the alternatives left
stay on the planner's response, which the explanation lists as assumptions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.logger import get_logger
from nl2sql.events import publish
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, PlanAlternative, PlanModel
from nl2sql.pipeline.state import SubgraphExecutionState

logger = get_logger("clarifier")

# The options a request offers at most: the default reading and two alternatives.
MAX_OPTIONS = 3
OPTION_IDS = "abc"
_BLOCKING = (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL)


class ClarifierNode:
    """Validates the planner's alternatives and ends the sub-query with a question when two or three readings remain.

    ``validator`` is the SQL agent's ``LogicalValidatorNode`` (its ``check``)
    and ``generator`` its ``GeneratorNode``, which renders an option's SQL so
    options that read the same are merged.
    """

    node_name = "clarifier"

    def __init__(self, ctx: Any, validator: Any, generator: Any):
        self.validator = validator
        self.generator = generator
        self.enabled = ctx.settings.clarify.enabled
        self._knowledge = getattr(ctx, "knowledge", None)

    def _sql(self, state: SubgraphExecutionState, plan: PlanModel) -> Optional[str]:
        """The SQL ``plan`` renders to for this sub-query, or None when the generator refuses it."""
        probe = state.model_copy(update={"ast_planner_response": ASTPlannerResponse(plan=plan)})
        response = (self.generator(probe) or {}).get("generator_response")
        return getattr(response, "sql_draft", None) or None

    def _options(self, state: SubgraphExecutionState, response: ASTPlannerResponse) -> List[PlanAlternative]:
        """The alternatives that pass the validator and render to SQL no earlier option has, in order."""
        seen = {self._sql(state, response.plan)}
        kept: List[PlanAlternative] = []
        for alternative in response.alternatives:
            if len(kept) >= MAX_OPTIONS - 1:
                break
            findings = self.validator.check(alternative.plan, state)
            if any(f.severity in _BLOCKING for f in findings):
                logger.info("An alternative reading failed validation (%s); it is not offered.",
                            ", ".join(sorted({f.error_code.value for f in findings})))
                continue
            sql = self._sql(state, alternative.plan)
            if sql is None or sql in seen:
                continue
            seen.add(sql)
            kept.append(alternative)
        return kept

    def _digest(self, datasource_id: str) -> str:
        if self._knowledge is not None:
            return self._knowledge.digest(datasource_id)
        from nl2sql.knowledge.merge import digest
        from nl2sql_sdk.knowledge import DatasourceKnowledge

        return digest(DatasourceKnowledge())

    def _ask(self, state: SubgraphExecutionState, response: ASTPlannerResponse,
             kept: List[PlanAlternative]) -> PipelineError:
        sub_query = state.sub_query
        labelled = [(None, response.plan)] + [(a.label, a.plan) for a in kept]
        options = [{"id": OPTION_IDS[n], "label": label, "plan": plan.model_dump(mode="json")}
                   for n, (label, plan) in enumerate(labelled)]
        publish("clarification.requested", {"sub_query_id": sub_query.id, "phrase": response.ambiguous_phrase,
                                            "options": len(options)})
        return PipelineError(
            node=self.node_name,
            message="This question can be read more than one way.",
            severity=ErrorSeverity.INFO,
            error_code=ErrorCode.NEEDS_CLARIFICATION,
            details={
                "phrase": response.ambiguous_phrase,
                "datasource_id": sub_query.datasource_id,
                "schema_version": sub_query.schema_version or "",
                "knowledge_digest": self._digest(sub_query.datasource_id),
                "sub_query": sub_query.model_dump(mode="json"),
                "options": options,
            },
        )

    def __call__(self, state: SubgraphExecutionState) -> Dict[str, Any]:
        response = state.ast_planner_response
        if response is None or response.plan is None or not response.alternatives:
            return {}
        kept = self._options(state, response)
        update: Dict[str, Any] = {"ast_planner_response": response.model_copy(update={"alternatives": kept})}
        ask = self.enabled and state.clarify and bool(kept) and bool(response.ambiguous_phrase)
        if not ask:
            return update
        error = self._ask(state, response, kept)
        return {**update, "errors": [error],
                "reasoning": [{"node": self.node_name, "content": [
                    f"'{response.ambiguous_phrase}' can be read {len(kept) + 1} ways; the run asks which."]}]}


def asked(state: SubgraphExecutionState) -> bool:
    """True when the clarifier ended the sub-query with a question."""
    return any(e.error_code == ErrorCode.NEEDS_CLARIFICATION for e in state.errors)
