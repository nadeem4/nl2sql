from __future__ import annotations

import json
import math
from typing import Dict, Optional, List, Any, TYPE_CHECKING, Tuple
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import SchemaInfo, TableInfo, ForeignKey, ColumnInfo
from nl2sql.vector_store import OrchestratorVectorStore
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.logger import get_logger

logger = get_logger("schema_node")

class SafeInspector:
    """Wrapper around SQLAlchemy inspector to handle errors gracefully."""

    def __init__(self, engine: Engine):
        """Initializes the SafeInspector.

        Args:
            engine: The SQLAlchemy engine to inspect.
        """
        self._inspector = sqlalchemy_inspect(engine)

    def get_table_names(self) -> List[str]:
        """Retrieves table names from the database.

        Returns:
            A list of table names, or an empty list if retrieval fails.
        """
        try:
            return self._inspector.get_table_names()
        except Exception as e:
            logger.warning(f"Failed to get table names: {e}")
            return []

    def get_columns(self, table_name: str) -> List[Dict[str, Any]]:
        """Retrieves columns for a specific table.

        Args:
            table_name: The name of the table.

        Returns:
            A list of column definitions, or an empty list if retrieval fails.
        """
        try:
            return self._inspector.get_columns(table_name)
        except Exception:
            return []

    def get_foreign_keys(self, table_name: str) -> List[Dict[str, Any]]:
        """Retrieves foreign keys for a specific table.

        Args:
            table_name: The name of the table.

        Returns:
            A list of foreign key definitions, or an empty list if retrieval fails.
        """
        try:
            return self._inspector.get_foreign_keys(table_name)
        except Exception:
            return []

class SchemaNode:
    """Retrieves schema information based on the user query."""

    def __init__(self, registry: DatasourceRegistry, vector_store: Optional[OrchestratorVectorStore] = None):
        """Initializes the SchemaNode.

        Args:
            registry: Datasource registry for accessing profiles and engines.
            vector_store: Optional vector store for schema retrieval.
        """
        self.registry = registry
        self.vector_store = vector_store

    def _get_search_candidates(self, state: GraphState) -> Optional[List[str]]:
        """Retrieves potential table candidates using vector search or pre-routed context.

        Args:
            state: The current graph state.

        Returns:
            A list of candidate table names, or None if no candidates found.
        """
        pre_routed_tables = state.candidate_tables
        ds_id = state.selected_datasource_id
        search_candidates = None
        
        if pre_routed_tables:
            logger.info(f"Using pre-routed tables for {ds_id}: {pre_routed_tables}")
            search_candidates = pre_routed_tables
        elif self.vector_store:
            try:
                search_q = state.user_query
                if state.intent:
                    extras = " ".join(state.intent.entities + state.intent.keywords)
                    if extras: 
                        search_q = f"{state.user_query} {extras}"
                
                ds_filter = [ds_id]
                search_candidates = self.vector_store.retrieve_table_names(search_q, datasource_id=ds_filter)
            except Exception:
                pass
        return search_candidates

    def _get_tables_and_related_tables(self, search_candidates: Optional[List[str]], inspector: SafeInspector, state: GraphState) -> Tuple[List[str], bool]:
        """Determines the final list of tables to include using selection and fallback logic.

        Args:
            search_candidates: Initial list of table candidates from vector search.
            inspector: SafeInspector instance for database introspection.
            state: The current graph state (used for query context in fallback).

        Returns:
            A tuple containing:
                - List of unique table names to include in the schema context.
                - Boolean indicating if schema drift was detected (fallback triggered).
        """
        all_tables = inspector.get_table_names()
        drift_detected = False
        
        candidates_to_use = search_candidates
        
        if not candidates_to_use:
            search_q = state.user_query
            if state.intent:
                extras = " ".join(state.intent.entities + state.intent.keywords)
                if extras: search_q = f"{state.user_query} {extras}"
            
            candidates_to_use = self._semantic_filter_fallback(all_tables, search_q)
            drift_detected = True 
        
        valid_candidates = [t for t in candidates_to_use if t in all_tables]
        if not valid_candidates:
             return [], drift_detected

        expanded = set(valid_candidates)
        all_tables_set = set(all_tables)

        for t in valid_candidates:
            fks = inspector.get_foreign_keys(t)
            for fk in fks:
                ref = fk.get("referred_table")
                if ref and ref in all_tables_set:
                    expanded.add(ref)

        return list(expanded), drift_detected

    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        """Computes cosine similarity between two vectors.

        Args:
            v1: First vector.
            v2: Second vector.

        Returns:
            Cosine similarity score between -1.0 and 1.0.
        """
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm_v1 = math.sqrt(sum(a * a for a in v1))
        norm_v2 = math.sqrt(sum(b * b for b in v2))
        return dot_product / (norm_v1 * norm_v2) if norm_v1 > 0 and norm_v2 > 0 else 0.0

    def _semantic_filter_fallback(self, all_tables: List[str], query: str, top_k: int = 10) -> List[str]:
        """Filters tables closer to the query using semantic embeddings.

        Used as a fallback when vector search fails to account for schema drift or missing index.

        Args:
            all_tables: List of all table names in the database.
            query: The user query to match against.
            top_k: Number of top matches to return.

        Returns:
            A list of the top k semantically relevant table names.
        """
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

    def _extract_schema_info(self, state: GraphState, tables: List[str], inspector: SafeInspector) -> List[TableInfo]:
        """Extracts detailed schema information for the selected list of tables.

        Args:
            state: The current graph state.
            tables: List of table names to extract info for.
            inspector: SafeInspector instance.

        Returns:
            A list of TableInfo objects containing columns and foreign keys.
        """
        table_infos = []
        
        for i, table in enumerate(tables):
            alias = f"t{i+1}"
            
            columns = []
            bs_columns = inspector.get_columns(table)
            for col in bs_columns:
                col_name = col.get("name")
                if not col_name: continue
                
                columns.append(ColumnInfo(
                    name=f"{alias}.{col_name}",
                    original_name=col_name,
                    type=str(col.get("type", "UNKNOWN"))
                ))
            
            fks = []
            bs_fks = inspector.get_foreign_keys(table)
            for fk in bs_fks:
                constrained = fk.get("constrained_columns", [])
                referred = fk.get("referred_columns", [])
                referred_tbl = fk.get("referred_table", "")
                
                if constrained and referred and referred_tbl:
                    fks.append(ForeignKey(
                        column=constrained[0],
                        referred_table=referred_tbl,
                        referred_column=referred[0]
                    ))
            
            table_infos.append(TableInfo(
                name=table,
                alias=alias,
                columns=columns,
                foreign_keys=fks
            ))
        return table_infos

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the schema retrieval step.

        Args:
            state: The current graph state.

        Returns:
            Dictionary updates for the graph state, including schema_info and system_events.
        """
        try:
            ds_id = state.selected_datasource_id
            
            search_candidates = self._get_search_candidates(state)

            engine = self.registry.get_engine(ds_id)
            inspector = SafeInspector(engine)

            ds_tables, drift_detected = self._get_tables_and_related_tables(search_candidates, inspector, state)
            
            table_infos = self._extract_schema_info(state, ds_tables, inspector)
            
            result = {
                "schema_info": SchemaInfo(tables=table_infos),
                "selected_datasource_id": ds_id,
                "reasoning": [{"node": "schema", "content": f"Retrieved {len(table_infos)} tables from {ds_id}."}],
                "system_events": []
            }
            
            if drift_detected:
                logger.warning(f"Schema Drift Detected for datasource {ds_id}. Triggering re-index recommendation.")
                result["system_events"].append("DRIFT_DETECTED")
                result["reasoning"].append({"node": "schema", "content": "Schema Drift Detected: Used semantic fallback.", "type": "warning"})
            
            return result

        except Exception as e:
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
