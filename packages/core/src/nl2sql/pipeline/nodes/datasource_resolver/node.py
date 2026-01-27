from __future__ import annotations

from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from nl2sql.pipeline.state import GraphState

from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.context import NL2SQLContext
from nl2sql.common.settings import settings
from nl2sql.pipeline.subgraphs import build_subgraph_registry
from .schemas import DatasourceResolverResponse, ResolvedDatasource


logger = get_logger("datasource_resolver")


class DatasourceResolverNode:
    """Resolves candidate datasources using vector search over datasource chunks."""

    def __init__(self, ctx: NL2SQLContext):
        self.node_name = self.__class__.__name__.lower().replace("node", "")
        self.vector_store = ctx.vector_store
        self.rbac = ctx.rbac
        self.ds_registry = ctx.ds_registry
        self.subgraph_specs = build_subgraph_registry(ctx)
        self.schema_store = ctx.schema_store

    def _resolve_unsupported(self, datasource_ids: list[str]) -> list[str]:
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

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        try:
            if not self.vector_store:
                return {
                    "datasource_resolver_response": DatasourceResolverResponse(),
                    "reasoning": [{"node": self.node_name, "content": "Vector store unavailable."}],
                }

            query = state.user_query
            docs = self.vector_store.retrieve_datasource_candidates(query, k=5)
            resolved_ids = []
            resolved_datasources = []
            schema_versions: Dict[str, str | None] = {}
            for doc in docs:
                ds_id = doc.metadata.get("datasource_id")
                if ds_id and ds_id not in resolved_ids:
                    resolved_ids.append(ds_id)
                    if ds_id not in schema_versions:
                        schema_versions[ds_id] = self._get_latest_schema_version(ds_id)
                    chunk_schema_version = doc.metadata.get("schema_version")
                    schema_version = schema_versions.get(ds_id)
                    resolved_datasources.append(
                        ResolvedDatasource(
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
                    )

            allowed_ids = self.rbac.get_allowed_datasources(state.user_context)
            filtered_ids = [ds_id for ds_id in resolved_ids if ds_id in allowed_ids]
            unsupported_ids = self._resolve_unsupported(resolved_ids)

            if not resolved_ids:
                return {
                    "datasource_resolver_response": DatasourceResolverResponse(
                        resolved_datasources=[],
                        allowed_datasource_ids=allowed_ids,
                        unsupported_datasource_ids=[],
                    ),
                    "errors": [
                        PipelineError(
                            node=self.node_name,
                            message="No datasource candidates resolved.",
                            severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                        )
                    ],
                }

            if not allowed_ids:
                return {
                    "datasource_resolver_response": DatasourceResolverResponse(
                        resolved_datasources=resolved_datasources,
                        allowed_datasource_ids=[],
                        unsupported_datasource_ids=unsupported_ids,
                    ),
                    "errors": [
                        PipelineError(
                            node=self.node_name,
                            message="Access denied: no allowed datasources.",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=ErrorCode.SECURITY_VIOLATION,
                        )
                    ],
                }

            if not filtered_ids:
                return {
                    "datasource_resolver_response": DatasourceResolverResponse(
                        resolved_datasources=resolved_datasources,
                        allowed_datasource_ids=allowed_ids,
                        unsupported_datasource_ids=unsupported_ids,
                    ),
                    "errors": [
                        PipelineError(
                            node=self.node_name,
                            message="Resolved datasources are not authorized.",
                            severity=ErrorSeverity.CRITICAL,
                            error_code=ErrorCode.SECURITY_VIOLATION,
                        )
                    ],
                }
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
                    return {
                        "datasource_resolver_response": DatasourceResolverResponse(
                            resolved_datasources=resolved_datasources,
                            allowed_datasource_ids=[],
                            unsupported_datasource_ids=unsupported_ids,
                        ),
                        "errors": [
                            PipelineError(
                                node=self.node_name,
                                message=message,
                                severity=ErrorSeverity.ERROR,
                                error_code=ErrorCode.INVALID_STATE,
                            )
                        ],
                    }
                if policy == "warn":
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
                    }
            return {
                "datasource_resolver_response": DatasourceResolverResponse(
                    resolved_datasources=resolved_datasources,
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
