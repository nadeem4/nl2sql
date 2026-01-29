from __future__ import annotations

from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from langchain_core.documents import Document
from nl2sql.auth import UserContext
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.common.settings import settings
from .schemas import DatasourceResolverResponse, ResolvedDatasource


logger = get_logger("datasource_resolver")


class DatasourceResolverNode:
    """Resolves candidate datasources using vector search over datasource chunks."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.vector_store = ctx.vector_store
        self.rbac = ctx.rbac
        self.ds_registry = ctx.ds_registry
        from nl2sql.pipeline.subgraphs import build_subgraph_registry
        self.subgraph_specs = build_subgraph_registry(ctx)
        self.schema_store = ctx.schema_store

    def _get_unsupported_datasources(self, datasource_ids: list[str]) -> list[str]:
        unsupported = []
        for ds_id in datasource_ids:
            try:
                caps = self.ds_registry.get_capabilities(ds_id)
            except Exception:
                unsupported.append(ds_id)
                continue

            is_supported = any(
                spec.required_capabilities.issubset(caps)
                for spec in self.subgraph_specs.values()
            )
            if not is_supported:
                unsupported.append(ds_id)
        return sorted(unsupported)

    def _get_latest_schema_version(self, datasource_id: str) -> str | None:
        try:
            latest = self.schema_store.get_latest_version(datasource_id)
        except Exception:
            return None
        return latest

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
                schema_versions[ds_id] = self._get_latest_schema_version(ds_id)
            chunk_schema_version = doc.metadata.get("schema_version")
            schema_version = schema_versions.get(ds_id)
            candidate_datasources[ds_id] = ResolvedDatasource(
                datasource_id=ds_id,
                metadata=dict(doc.metadata),
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
        

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        try:
            if not self.vector_store:
                return {
                    "datasource_resolver_response": DatasourceResolverResponse(),
                    "reasoning": [{"node": self.node_name, "content": "Vector store unavailable."}],
                }

            query = state.user_query
            candidate_docs = self.vector_store.retrieve_datasource_candidates(query, k=5)
            candidate_datasources = self._get_candidate_datasources(candidate_docs)
            candidate_ids = list(candidate_datasources.keys())
            if not candidate_ids:
                return self._error_response(
                    resolved_datasources=[],
                    allowed_ids=[],
                    unsupported_ids=[],
                    message="No datasource candidates resolved.",
                    severity=ErrorSeverity.ERROR,
                    error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                )
            unsupported_ids = self._get_unsupported_datasources(candidate_ids)
            allowed_ids = self._get_allowed_datasource_ids(state.user_context, candidate_ids)
            if not allowed_ids:
                return self._error_response(
                    resolved_datasources=list(candidate_datasources.values()),
                    allowed_ids=[],
                    unsupported_ids=unsupported_ids,
                    message="No allowed datasources.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
                )

            authorized_ids = allowed_ids if "*" in allowed_ids else [
                ds_id for ds_id in candidate_ids if ds_id in allowed_ids
            ]
            authorized_ids = [ds_id for ds_id in authorized_ids if ds_id not in unsupported_ids]
            if not authorized_ids:
                return self._error_response(
                    resolved_datasources=list(candidate_datasources.values()),
                    allowed_ids=allowed_ids,
                    unsupported_ids=unsupported_ids,
                    message="Resolved datasources are not authorized.",
                    severity=ErrorSeverity.CRITICAL,
                    error_code=ErrorCode.SECURITY_VIOLATION,
                )

            schema_version_mismatch_response = self._apply_schema_version_mismatch_policy(
                list(candidate_datasources.values()),
                allowed_ids,
                unsupported_ids,
            )
            if schema_version_mismatch_response:
                return schema_version_mismatch_response
            
            resolved = [
                candidate_datasources[ds_id] for ds_id in authorized_ids
            ]
            return {
                "datasource_resolver_response": DatasourceResolverResponse(
                    resolved_datasources=resolved,
                    allowed_datasource_ids=allowed_ids,
                    unsupported_datasource_ids=unsupported_ids,
                ),
                "reasoning": [{"node": self.node_name, "content": "Ranked by vector similarity."}],
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
