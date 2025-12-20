from __future__ import annotations
from typing import Dict, Any, Callable, Union, TYPE_CHECKING
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from nl2sql.logger import get_logger
from nl2sql.errors import PipelineError, ErrorSeverity, ErrorCode
from .schemas import IntentResponse
from .prompts import INTENT_PROMPT

logger = get_logger("intent")

LLMCallable = Union[Callable[[str], Any], Runnable]


class IntentNode:
    def __init__(self, llm: LLMCallable):
        self.llm = llm
        self.prompt = ChatPromptTemplate.from_template(INTENT_PROMPT)
        self.chain = self.prompt | self.llm

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        node_name = "intent"

        try:
            response: IntentResponse = self.chain.invoke({
                "user_query": state.user_query
            })

            enriched_terms = (
                response.keywords
                + response.synonyms
                + [e.name for e in response.entities]
                + [e for group in response.entity_roles for e in group.entity_ids]
            )

            # Convert back to dict for state consistency if needed, or keep as object
            entity_roles = {group.role.value: group.entity_ids for group in response.entity_roles}

            return {
                "user_query": response.canonical_query,
                "response_type": response.response_type,
                "analysis_intent": response.analysis_intent,
                "time_scope": response.time_scope,
                "entity_roles": entity_roles,
                "entities": response.entities, # This is now List[Entity] from the LLM
                "ambiguity_level": response.ambiguity_level,
                "enriched_terms": enriched_terms,
                "reasoning": [{
                    "node": node_name,
                    "content": f"Intent={response.analysis_intent}, TimeScope={response.time_scope}, Ambiguity={response.ambiguity_level}"
                }]
            }

        except Exception as exc:
            return {
                "response_type": "tabular",
                "enriched_terms": [],
                "errors": [
                    PipelineError(
                        node=node_name,
                        message=f"Intent extraction failed: {exc}",
                        severity=ErrorSeverity.WARNING,
                        error_code=ErrorCode.INTENT_EXTRACTION_FAILED,
                        stack_trace=str(exc)
                    )
                ],
                "reasoning": [{
                    "node": node_name,
                    "content": f"Intent extraction failed. Defaulting behavior applied."
                }]
            }
