from __future__ import annotations

import json
from typing import Dict, Optional, List, Any, TYPE_CHECKING
from sqlalchemy import inspect

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import SchemaInfo, TableInfo, ForeignKey, ColumnInfo
from nl2sql.vector_store import OrchestratorVectorStore
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.errors import PipelineError, ErrorSeverity

from nl2sql.logger import get_logger

logger = get_logger("schema_node")


class SchemaNode:
    """
    Retrieves schema information (tables, columns, foreign keys) based on the user query.
    
    Uses vector store for relevant table selection if available, otherwise retrieves all tables.
    Also handles assigning aliases to tables and columns for the planner.
    """

    def __init__(self, registry: DatasourceRegistry, vector_store: Optional[OrchestratorVectorStore] = None):
        """
        Initializes the SchemaNode.

        Args:
            registry: Datasource registry for accessing profiles and engines.
            vector_store: Optional vector store for schema retrieval.
        """
        self.registry = registry
        self.vector_store = vector_store

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """
        Executes the schema retrieval step.

        Args:
            state: The current graph state.

        Returns:
            Dictionary updates for the graph state with schema information.
        """
        
        try:
            if state.errors:
                return {}

            if not state.selected_datasource_id:
                return {"validation": {"retry_routing": True}}

            target_ds_id = state.selected_datasource_id
            
            ds_ids = {target_ds_id}
            
            search_candidates = None
            
            # Check for Pre-Routed Candidate Tables (from Orchestrator/Decomposer)
            # Find the SubQuery corresponding to this datasource_id
            pre_routed_tables = None
            if state.sub_queries:
                 for sq in state.sub_queries:
                     if hasattr(sq, "datasource_id") and sq.datasource_id == target_ds_id:
                         if sq.candidate_tables:
                             pre_routed_tables = sq.candidate_tables
                             break
            
            if pre_routed_tables:
                search_candidates = pre_routed_tables
                logger.info(f"Using pre-routed tables for {target_ds_id}: {pre_routed_tables}")
            elif self.vector_store:
                 # Fallback to local vector search if no pre-routed tables (L3 Case)
                 try:
                    search_q = state.user_query
                    if state.intent:
                         extras = " ".join(state.intent.entities + state.intent.keywords)
                         if extras: search_q = f"{state.user_query} {extras}"
                    
                    ds_filter = [target_ds_id]
                    search_candidates = self.vector_store.retrieve_table_names(search_q, datasource_id=ds_filter)
                 except:
                     pass

            final_table_infos = []

            ds_id = target_ds_id
            try:
                engine = self.registry.get_engine(ds_id)
                inspector = inspect(engine)
                ds_tables = inspector.get_table_names()
                
                if search_candidates:
                    relevant_tables = [t for t in search_candidates if t in ds_tables]
                    if relevant_tables:
                        expanded = set(relevant_tables)
                        for t in relevant_tables:
                            try:
                                for fk in inspector.get_foreign_keys(t):
                                    ref = fk.get("referred_table")
                                    if ref and ref in ds_tables:
                                        expanded.add(ref)
                            except: pass
                        ds_tables = list(expanded)
                    else:
                        if not relevant_tables: 
                            if not search_candidates:
                                pass
                            else:
                                ds_tables = [] 
                
                for i, table in enumerate(ds_tables):
                    try:
                        alias =  f"t{i+1}" 
                        columns = []
                        for col in inspector.get_columns(table):
                            col_name = col["name"]
                            col_type = str(col["type"])
                            columns.append(ColumnInfo(
                                name=f"{alias}.{col_name}",
                                original_name=col_name,
                                type=col_type
                            ))
                        
                        fks = [
                            ForeignKey(
                                constrained_columns=fk["constrained_columns"],
                                referred_table=fk["referred_table"],
                                referred_columns=fk["referred_columns"]
                            )
                            for fk in inspector.get_foreign_keys(table)
                        ]
                        
                        table_info =  TableInfo(
                            name=table,
                            alias=alias,
                            columns=columns,
                            foreign_keys=fks
                        )
                        final_table_infos.append(table_info)
                    except Exception:
                        pass 
            except Exception:
                pass 
                
            
            return {
                "schema_info": SchemaInfo(tables=final_table_infos),
                "selected_datasource_id": target_ds_id,
                "reasoning": [{"node": "schema", "content": f"Retrieved {len(final_table_infos)} tables from {target_ds_id}."}]
            }

        except Exception as e:
            return {
                "errors": [
                    PipelineError(
                        node="schema",
                        message=f"Schema retrieval failed: {e}",
                        severity=ErrorSeverity.CRITICAL,
                        error_code="SCHEMA_RETRIEVAL_FAILED",
                        stack_trace=str(e)
                    )
                ]
            }
