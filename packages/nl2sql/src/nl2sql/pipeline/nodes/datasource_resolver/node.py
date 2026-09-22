from __future__ import annotations

import json
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from langchain_core.documents import Document
from nl2sql.auth import UserContext
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.common.settings import settings
from nl2sql.llm.wires import structured
from .prompts import ANSWERABILITY_PROMPT
from .schemas import AnswerabilityResponse, DatasourceResolverResponse, ResolvedDatasource


logger = get_logger("datasource_resolver")


class DatasourceResolverNode:
    """Picks the datasources a question goes to, and refuses what none can answer.

    In order:

    1. Candidates. An explicit ``datasource_id`` is used as given. With exactly
       one datasource registered, that one is used without a vector search.
       Otherwise a vector search over the datasource entries picks them.
    2. The role check: candidates the caller's role may not read are dropped;
       none left is a ``SECURITY_VIOLATION``, before any model call.
    3. The schema-version mismatch policy.
    4. The answerability check: one small structured LLM call over the allowed
       candidates' descriptions and table lists. An empty verdict ends the run
       with ``QUESTION_NOT_ANSWERABLE`` before the decomposer.
    """

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.vector_store = ctx.vector_store
        self.rbac = ctx.rbac
        self.ds_registry = ctx.ds_registry
        self.schema_store = ctx.schema_store
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
        schema_versions: Dict[str, str | None] = {}
        for doc in candidate_docs:
            ds_id = doc.metadata.get("datasource_id")
            if not ds_id or ds_id in candidate_datasources:
                continue
            if ds_id not in schema_versions:
                schema_versions[ds_id] = self.schema_store.get_latest_version(ds_id)
            chunk_schema_version = doc.metadata.get("schema_version")
            schema_version = schema_versions.get(ds_id)
            candidate_datasources[ds_id] = ResolvedDatasource(
                datasource_id=ds_id,
                # Sorted: the decomposer prints this into its prompt, and the
                # vector store returns it in no fixed order.
                metadata=dict(sorted(doc.metadata.items())),
                schema_version=schema_version,
                chunk_schema_version=chunk_schema_version,
                schema_version_mismatch=bool(
                    chunk_schema_version
                    and schema_version
                    and chunk_schema_version != schema_version
                ),
            )
        return candidate_datasources

    def _get_allowed_datasource_ids(
        self,
        user_context: UserContext,
        candidate_ids: list[str],
    ) -> list[str]:
        allowed_ids = self.rbac.get_allowed_datasources(user_context)
        if not allowed_ids:
            return []
        if "*" in allowed_ids:
            return candidate_ids
        return [ds_id for ds_id in candidate_ids if ds_id in allowed_ids]


    def _apply_schema_version_mismatch_policy(self, resolved_datasources: list[ResolvedDatasource], allowed_ids: list[str], unsupported_ids: list[str]):
        mismatches = [
                ds.datasource_id
                for ds in resolved_datasources
                if ds.schema_version_mismatch
        ]
        if mismatches:
            policy = (settings.schema_version_mismatch_policy or "warn").lower()
            message = (
                "Schema version mismatch for datasources: "
                + ", ".join(sorted(mismatches))
            )
            if policy == "fail":
                return self._error_response(
                    resolved_datasources=[],
                    allowed_ids=[],
                    unsupported_ids=[],
                    message=message,
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.INVALID_STATE,
                )
            elif policy == "warn":
                return {
                    "datasource_resolver_response": DatasourceResolverResponse(
                        resolved_datasources=resolved_datasources,
                        allowed_datasource_ids=allowed_ids,
                        unsupported_datasource_ids=unsupported_ids,
                    ),
                    "reasoning": [
                        {
                            "node": self.node_name,
                            "content": message,
                            "type": "warning",
                        }
                    ],
                        "warnings": [
                            {
                                "node": self.node_name,
                                "content": message,
                            }
                        ],
                }

    def _direct_candidate(self, datasource_id: str) -> ResolvedDatasource:
        """A datasource chosen without a search: the override, or the only one."""
        return ResolvedDatasource(
            datasource_id=datasource_id,
            metadata={},
            schema_version=self.schema_store.get_latest_version(datasource_id),
        )

    def _answerability_input(self, datasource_ids: list[str]) -> str:
        """Each datasource's description and table list, serialized deterministically.

        Sorted by id, keys sorted, tables sorted: the same datasources give the
        same bytes whatever order the search returned them in, so this block
        is a stable, cacheable prompt prefix.
        """
        entries = []
        for ds_id in sorted(datasource_ids):
            snapshot = self.schema_store.get_latest_snapshot(ds_id)
            description = (snapshot.metadata.description or "") if snapshot else ""
            # Bare table names: the check needs the topic, not the qualified name.
            tables = sorted({t.table.table_name for t in snapshot.contract.tables.values()}) if snapshot else []
            entries.append({"description": description.strip(), "id": ds_id, "tables": tables})
        return json.dumps(entries, sort_keys=True, ensure_ascii=False)

    def _judge_answerability(self, question: str, datasource_ids: list[str]) -> AnswerabilityResponse:
        return self.answerability_chain.invoke(
            {"datasources": self._answerability_input(datasource_ids), "user_query": question}
        )

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        try:
            registered_ids = self.ds_registry.list_ids()
            if state.datasource_id:
                unsupported_ids = self._get_unsupported_datasources([state.datasource_id])
                if unsupported_ids:
                    return self._error_response(
                        resolved_datasources=[],
                        allowed_ids=[],
                        unsupported_ids=unsupported_ids,
                        message=f"Datasource not found: {state.datasource_id}.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.INVALID_STATE,
                    )
                candidates = [self._direct_candidate(state.datasource_id)]
                how = "Using explicit datasource override."
            elif len(registered_ids) == 1:
                # Searching one document for every question can only ever
                # return it, and fails when the vector store is missing.
                candidates = [self._direct_candidate(registered_ids[0])]
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
                candidate_docs = self.vector_store.retrieve_datasource_candidates(state.user_query, k=5)
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

            mismatch_response = self._apply_schema_version_mismatch_policy(
                candidates,
                allowed_ids,
                unsupported_ids,
            ) or {}
            if "errors" in mismatch_response:
                return mismatch_response

            verdict = self._judge_answerability(state.user_query, allowed_ids)
            if not verdict.answerable_datasource_ids:
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

            return {
                "datasource_resolver_response": DatasourceResolverResponse(
                    resolved_datasources=candidates,
                    allowed_datasource_ids=allowed_ids,
                    unsupported_datasource_ids=unsupported_ids,
                ),
                "reasoning": [
                    {"node": self.node_name, "content": how},
                    {"node": self.node_name, "content": f"Answerable: {verdict.reason}"},
                    *mismatch_response.get("reasoning", []),
                ],
                **({"warnings": mismatch_response["warnings"]} if "warnings" in mismatch_response else {}),
            }
        except Exception as exc:
            logger.error(f"Datasource resolver failed: {exc}")
            return {
                "datasource_resolver_response": DatasourceResolverResponse(),
                "errors": [
                    PipelineError(
                        node=self.node_name,
                        message=f"Datasource resolution failed: {exc}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                    )
                ],
            }
