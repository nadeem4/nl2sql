from __future__ import annotations

import math
from typing import Dict, Optional, List, Any, TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from nl2sql.core.schemas import GraphState

from .schemas import SchemaInfo, TableInfo, ForeignKey, ColumnInfo
from nl2sql.core.vector_store import OrchestratorVectorStore
from nl2sql.core.datasource_registry import DatasourceRegistry
from nl2sql_adapter_sdk import DatasourceAdapter, SchemaMetadata
from nl2sql.core.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.core.logger import get_logger

logger = get_logger("schema_node")


class SchemaNode:
    """Retrieves schema information based on the user query."""

    def __init__(self, registry: DatasourceRegistry, vector_store: Optional[OrchestratorVectorStore] = None):
        """Initializes the SchemaNode."""
        self.registry = registry
        self.vector_store = vector_store

    def _get_search_candidates(self, state: GraphState) -> Optional[List[str]]:
        """Retrieves potential table candidates using entity_mapping."""
        mapping = state.entity_mapping
        ds_id = state.selected_datasource_id
        
        if mapping and ds_id:
            tables = set()
            for m in mapping:
                if m.datasource_id == ds_id and m.candidate_tables:
                    tables.update(m.candidate_tables)
            
            if tables:
                logger.info(f"Using mapped tables for {ds_id}: {tables}")
                return list(tables)
        return None

    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm_v1 = math.sqrt(sum(a * a for a in v1))
        norm_v2 = math.sqrt(sum(b * b for b in v2))
        return dot_product / (norm_v1 * norm_v2) if norm_v1 > 0 and norm_v2 > 0 else 0.0

    def _semantic_filter_fallback(self, adapter: DataSourceAdapter, query: str, top_k: int = 10) -> List[str]:
        """Filters tables closer to the query using semantic embeddings."""
        # Note: Ideally Adapter has 'list_tables()', but get_schema() works too.
        # But for efficiency, we really need a lightweight list.
        # For now, we fetch full schema to get names.
        full_schema = adapter.fetch_schema()
        all_tables = [t.name for t in full_schema.tables]

        if not self.vector_store or not self.vector_store.embeddings:
            logger.warning("No embeddings model available for semantic fallback.")
            return all_tables[:top_k]

        try:
            query_embedding = self.vector_store.embeddings.embed_query(query)
            table_embeddings = self.vector_store.embeddings.embed_documents(all_tables)
            
            scores = []
            for i, tbl_emb in enumerate(table_embeddings):
                score = self._cosine_similarity(query_embedding, tbl_emb)
                scores.append((score, all_tables[i]))
            
            scores.sort(key=lambda x: x[0], reverse=True)
            top_matches = [t for s, t in scores[:top_k]]
            
            logger.info(f"Semantic fallback selected: {top_matches}")
            return top_matches
        except Exception as e:
            logger.error(f"Semantic fallback failed: {e}")
            return all_tables[:top_k]

    def _convert_to_schema_info(self, sdk_schema: SchemaMetadata) -> List[TableInfo]:
        """Converts SDK SchemaMetadata to internal TableInfo format."""
        table_infos = []
        for i, table_def in enumerate(sdk_schema.tables):
            alias = f"t{i+1}"
            columns = []
            for col in table_def.columns:
                 columns.append(ColumnInfo(
                     name=f"{alias}.{col.name}",
                     original_name=col.name,
                     type=col.type
                 ))
            
            # Note: Foreign Keys are not yet in SDK models.py (Simplified for V1).
            # We assume Planner can infer joins by name or we update SDK later.
            table_infos.append(TableInfo(
                name=table_def.name,
                alias=alias,
                columns=columns,
                foreign_keys=[] 
            ))
        return table_infos

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the schema retrieval step."""
        try:
            ds_id = state.selected_datasource_id
            search_candidates = self._get_search_candidates(state)
            
            # 1. Get Adapter
            adapter = self.registry.get_adapter(ds_id)
            
            # 2. Determine Tables
            drift_detected = False
            target_tables = search_candidates
            
            if not target_tables:
                logger.info("No candidates from Decomposer. Falling back to semantic search.")
                target_tables = self._semantic_filter_fallback(adapter, state.user_query)
                drift_detected = True
            
            # 3. Fetch Schema via SDK
            # Note: SDK v1 fetch_schema() gets everything. 
            # We might filter locally or update SDK to accept table_names if needed.
            # Assuming full fetch for now or we filter sdk_schema.tables manually.
            sdk_schema = adapter.fetch_schema()
            
            # Filter locally if needed
            if target_tables:
                 sdk_schema.tables = [t for t in sdk_schema.tables if t.name in target_tables]
            
            # 4. Convert
            table_infos = self._convert_to_schema_info(sdk_schema)
            
            result = {
                "schema_info": SchemaInfo(tables=table_infos),
                "selected_datasource_id": ds_id,
                "reasoning": [{"node": "schema", "content": f"Retrieved {len(table_infos)} tables via Adapter."}],
                "system_events": []
            }
            
            if drift_detected:
                result["system_events"].append("DRIFT_DETECTED")
                result["reasoning"].append({"node": "schema", "content": "Schema Drift Detected: Used semantic fallback.", "type": "warning"})
            
            return result

        except Exception as e:
            logger.exception(f"SchemaNode failed: {e}")
            return {
                "errors": [
                    PipelineError(
                        node="schema",
                        message=f"Schema retrieval failed: {e}",
                        severity=ErrorSeverity.CRITICAL,
                        error_code=ErrorCode.SCHEMA_RETRIEVAL_FAILED,
                        stack_trace=str(e)
                    )
                ]
            }
