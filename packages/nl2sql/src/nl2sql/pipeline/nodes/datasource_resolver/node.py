from __future__ import annotations

import json
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from langchain_core.documents import Document
from nl2sql.policies import UserContext
from nl2sql.common.logger import get_logger
from nl2sql.runtime import Runtime
from nl2sql.llm.failures import provider_error
from nl2sql.llm.wires import structured
from .prompts import ANSWERABILITY_PROMPT
from .schemas import AnswerabilityResponse, DatasourceResolverResponse, ResolvedDatasource


logger = get_logger("datasource_resolver")

# Table names per datasource the decomposer is shown; the rest are counted, so
# a large schema does not bloat its prompt.
MAX_DECOMPOSER_TABLES = 30


class DatasourceResolverNode:
    """Picks the datasources a question goes to, and refuses what none can answer.

    In order:

    1. Candidates. An explicit ``datasource_id`` is used as given. With exactly
       one datasource registered, that one is used without a vector search.
       Otherwise a vector search over the datasource entries picks them.
    2. The role check: candidates the caller's role may not read are dropped;
       none left is a ``SECURITY_VIOLATION``, before any model call.
    3. The answerability check: one small structured LLM call over the allowed
       candidates' descriptions and table lists. An empty verdict ends the run
       with ``QUESTION_NOT_ANSWERABLE`` before the decomposer. After a vector
       search only the datasources the verdict names (and the role may read)
       go forward, each with its description and a capped table list for the
       decomposer.

    Each datasource's schema version and snapshot are its live build's, read
    through the run's pinned index pointers (``ctx.vector_store``), never the
    state store's newest snapshot.
    """

    def __init__(self, ctx: Runtime):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.vector_store = ctx.vector_store
        self.policy_engine = ctx.policy_engine
        self.ds_registry = ctx.ds_registry
        # Registered as agent "datasourceresolver"; falls back to "default".
        self.llm = ctx.llm_registry.get_llm(self.node_name)
        self.answerability_chain = ANSWERABILITY_PROMPT | structured(self.llm, AnswerabilityResponse)

    def _index_is_empty(self) -> bool:
        is_empty = getattr(self.vector_store, "is_empty", None)
        try:
            return bool(is_empty()) if callable(is_empty) else False
        except Exception:
            return False

    def _get_unsupported_datasources(self, datasource_ids: list[str]) -> list[str]:
        available_ds_ids = self.ds_registry.list_ids()
        unsupported = [ds_id for ds_id in datasource_ids if ds_id not in available_ds_ids]
        return sorted(unsupported)

    def _error_response(
        self,
        resolved_datasources: list[ResolvedDatasource],
        allowed_ids: list[str],
        unsupported_ids: list[str],
        message: str,
        severity: ErrorSeverity,
        error_code: ErrorCode,
    ) -> Dict[str, Any]:
        return {
            "datasource_resolver_response": DatasourceResolverResponse(
                resolved_datasources=resolved_datasources,
                allowed_datasource_ids=allowed_ids,
                unsupported_datasource_ids=unsupported_ids,
            ),
            "errors": [
                PipelineError(
                    node=self.node_name,
                    message=message,
                    severity=severity,
                    error_code=error_code,
                )
            ],
        }

    def _get_candidate_datasources(
        self,
        candidate_docs: list[Document],
    ) -> Dict[str, ResolvedDatasource]:
        candidate_datasources: Dict[str, ResolvedDatasource] = {}
        for doc in candidate_docs:
            ds_id = doc.metadata.get("datasource_id")
            if not ds_id or ds_id in candidate_datasources:
                continue
            candidate_datasources[ds_id] = ResolvedDatasource(
                datasource_id=ds_id,
                # Sorted: the decomposer prints this into its prompt, and the
                # vector store returns it in no fixed order.
                metadata=dict(sorted(doc.metadata.items())),
                schema_version=self.vector_store.schema_version(ds_id),
            )
        return candidate_datasources

    def _get_allowed_datasource_ids(
        self,
        user_context: UserContext,
        candidate_ids: list[str],
    ) -> list[str]:
        return [ds_id for ds_id in candidate_ids
                if self.policy_engine.scope(user_context, ds_id).reaches_datasource()]


    def _direct_candidate(self, datasource_id: str) -> ResolvedDatasource:
        """A datasource chosen without a search: the override, or the only one."""
        return ResolvedDatasource(
            datasource_id=datasource_id,
            metadata={},
            schema_version=self.vector_store.schema_version(datasource_id),
        )

    def _summary(self, user_context: UserContext, datasource_id: str) -> tuple[str, list[str]]:
        """The datasource's description and the sorted, bare names of the tables the caller may read.

        A table the caller's scope denies is never named to a model: not to
        the answerability judge, not to the decomposer.
        """
        snapshot = self.vector_store.snapshot(datasource_id)
        if not snapshot:
            return "", []
        description = (snapshot.metadata.description or "").strip()
        scope = self.policy_engine.scope(user_context, datasource_id)
        # Bare table names: routing needs the topic, not the qualified name.
        tables = sorted({t.table.table_name for t in snapshot.contract.tables.values()
                         if scope.allows(t.table.schema_name, t.table.table_name)})
        return description, tables

    def _answerability_input(self, user_context: UserContext, datasource_ids: list[str]) -> str:
        """Each datasource's description and table list, serialized deterministically.

        Sorted by id, keys sorted, tables sorted: the same datasources give the
        same bytes whatever order the search returned them in, so this block
        is a stable, cacheable prompt prefix.
        """
        entries = []
        for ds_id in sorted(datasource_ids):
            description, tables = self._summary(user_context, ds_id)
            entries.append({"description": description, "id": ds_id, "tables": tables})
        return json.dumps(entries, sort_keys=True, ensure_ascii=False)

    def _with_context(self, user_context: UserContext, datasource: ResolvedDatasource) -> ResolvedDatasource:
        """The datasource with its description and a capped table list, for the decomposer."""
        description, tables = self._summary(user_context, datasource.datasource_id)
        return datasource.model_copy(update={
            "description": description,
            "tables": tables[:MAX_DECOMPOSER_TABLES],
            "more_tables": max(0, len(tables) - MAX_DECOMPOSER_TABLES),
        })

    def _judge_answerability(self, user_context: UserContext, question: str,
                             datasource_ids: list[str]) -> AnswerabilityResponse:
        return self.answerability_chain.invoke(
            {"datasources": self._answerability_input(user_context, datasource_ids), "user_query": question}
        )

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        searches: list[Dict[str, Any]] = []
        out = self._resolve(state, searches)
        out["retrieval"] = self._retrieval(state, searches)
        return out

    def _retrieval(self, state: GraphState, searches: list[Dict[str, Any]]) -> Dict[str, Any]:
        """What the vector search retrieved, for the run trace, or why it did not run.

        Not a state field: LangGraph drops the key, the trace keeps the node's
        whole return. It names datasources and entry ids, never entry text.
        """
        if searches:
            return {"skipped": False, "query": state.user_query, "searches": searches}
        if state.datasource_id:
            return {"skipped": True, "reason": "An explicit datasource_id was given, so no search ran."}
        try:
            single = len(self.ds_registry.list_ids()) == 1
        except Exception:
            single = False
        if single:
            return {"skipped": True, "reason": "single datasource: vector search skipped"}
        if not self.vector_store:
            return {"skipped": True, "reason": "No vector store is configured."}
        return {"skipped": False, "query": state.user_query, "searches": []}

    def _resolve(self, state: GraphState, searches: list[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            registered_ids = self.ds_registry.list_ids()
            if state.datasource_id:
                unsupported_ids = self._get_unsupported_datasources([state.datasource_id])
                if unsupported_ids:
                    unavailable = getattr(self.ds_registry, "unavailable", dict)()
                    reason = unavailable.get(state.datasource_id)
                    message = (
                        f"Datasource '{state.datasource_id}' is unavailable: {reason}"
                        if reason is not None
                        else f"Datasource not found: {state.datasource_id}."
                    )
                    return self._error_response(
                        resolved_datasources=[],
                        allowed_ids=[],
                        unsupported_ids=unsupported_ids,
                        message=message,
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.INVALID_STATE,
                    )
                candidates = [self._direct_candidate(state.datasource_id)]
                searched = False
                how = "Using explicit datasource override."
            elif len(registered_ids) == 1:
                # Searching one document for every question can only ever
                # return it, and fails when the vector store is missing.
                candidates = [self._direct_candidate(registered_ids[0])]
                searched = False
                how = "One datasource is registered, so it is used without a vector search."
            else:
                if not self.vector_store:
                    return self._error_response(
                        resolved_datasources=[],
                        allowed_ids=[],
                        unsupported_ids=[],
                        message=(
                            "Vector store unavailable, so no datasource can be matched "
                            "among the registered ones."
                        ),
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                    )
                candidate_docs = self.vector_store.retrieve_datasource_candidates(
                    state.user_query, k=5, explain=searches
                )
                candidates = list(self._get_candidate_datasources(candidate_docs).values())
                if not candidates:
                    message = "No datasource candidates resolved."
                    if self._index_is_empty():
                        # Said plainly, because an empty index fails every question
                        # while the rest of the system (the schema snapshot, the
                        # playground's schema panel) looks healthy.
                        message = (
                            "The vector index is empty, so no datasource can be matched. "
                            "Re-index with `nl2sql index` (in the playground, use Rebuild)."
                        )
                    return self._error_response(
                        resolved_datasources=[],
                        allowed_ids=[],
                        unsupported_ids=[],
                        message=message,
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                    )
                searched = True
                how = "Ranked by vector similarity."

            candidate_ids = [c.datasource_id for c in candidates]
            unsupported_ids = self._get_unsupported_datasources(candidate_ids)

            # The role check comes before the model call: a forbidden question
            # gets the RBAC refusal, and the model never sees a datasource the
            # role may not read.
            allowed_ids = self._get_allowed_datasource_ids(state.user_context, candidate_ids)
            if not allowed_ids:
                return self._error_response(
                    resolved_datasources=candidates,
                    allowed_ids=[],
                    unsupported_ids=unsupported_ids,
                    message="Datasource not allowed." if len(candidates) == 1 else "No allowed datasources.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
                )

            verdict = self._judge_answerability(state.user_context, state.user_query, allowed_ids)
            if searched:
                # Only what the verdict accepted goes forward, and only what
                # the role may read: the verdict cannot add a datasource.
                answerable = set(verdict.answerable_datasource_ids)
                forward_ids = [ds_id for ds_id in allowed_ids if ds_id in answerable]
            else:
                # One candidate, chosen without a search: a non-empty verdict
                # accepts it.
                forward_ids = allowed_ids if verdict.answerable_datasource_ids else []
            if not forward_ids:
                response = self._error_response(
                    resolved_datasources=candidates,
                    allowed_ids=[],
                    unsupported_ids=unsupported_ids,
                    message=(
                        "This question can't be answered from the connected data "
                        f"({', '.join(sorted(allowed_ids))}). Ask about what that data holds."
                    ),
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.QUESTION_NOT_ANSWERABLE,
                )
                response["reasoning"] = [{"node": self.node_name, "content": f"Not answerable: {verdict.reason}"}]
                return response

            # Sorted by id: the decomposer prints these into its prompt, and
            # the vector store returns them in no fixed order.
            forwarded = sorted(
                (self._with_context(state.user_context, c) for c in candidates if c.datasource_id in forward_ids),
                key=lambda c: c.datasource_id,
            )
            return {
                "datasource_resolver_response": DatasourceResolverResponse(
                    resolved_datasources=forwarded,
                    allowed_datasource_ids=sorted(forward_ids),
                    unsupported_datasource_ids=unsupported_ids,
                ),
                "reasoning": [
                    {"node": self.node_name, "content": how},
                    {"node": self.node_name, "content": f"Answerable: {verdict.reason}"},
                ],
            }
        except Exception as exc:
            logger.error(f"Datasource resolver failed: {exc}")
            return {
                "datasource_resolver_response": DatasourceResolverResponse(),
                "errors": [
                    provider_error(self.node_name, exc) or PipelineError(
                        node=self.node_name,
                        message=f"Datasource resolution failed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                    )
                ],
            }
