from __future__ import annotations

import json
from typing import Dict, Optional, List, Any, TYPE_CHECKING
from sqlalchemy import inspect

if TYPE_CHECKING:
    from nl2sql.schemas import GraphState

from .schemas import SchemaInfo, TableInfo, ForeignKey
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.datasource_registry import DatasourceRegistry

from nl2sql.logger import get_logger

logger = get_logger("schema_node")


class SchemaNode:
    """
    Retrieves schema information (tables, columns, foreign keys) based on the user query.
    
    Uses vector store for relevant table selection if available, otherwise retrieves all tables.
    Also handles assigning aliases to tables and columns for the planner.
    """

    def __init__(self, registry: DatasourceRegistry, vector_store: Optional[SchemaVectorStore] = None):
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
        node_name = "schema"
        
        try:
            if state.errors:
                return {}

            if not state.datasource_id:
                raise ValueError("No datasource_id in state. Router must run before SchemaNode.")

            ds_ids = state.datasource_id
            
            # 1. Determine Search Query (Once)
            search_candidates = None
            retrieved_tables_update = None
            
            if self.vector_store:
                 try:
                    search_q = state.user_query
                    if state.validation.get("intent"):
                         intent = state.validation["intent"]
                         if isinstance(intent, str): intent = json.loads(intent)
                         extras = " ".join(intent.get("entities", []) + intent.get("keywords", []))
                         if extras: search_q = f"{state.user_query} {extras}"
                    
                    ds_filter = list(ds_ids) 
                    search_candidates = self.vector_store.retrieve(search_q, datasource_id=ds_filter)
                    retrieved_tables_update = search_candidates
                 except:
                     pass

            final_table_infos = []

            for ds_id in ds_ids:
                try:
                    engine = self.registry.get_engine(ds_id)
                    inspector = inspect(engine)
                    ds_tables = inspector.get_table_names()
                    
                    # Filter
                    if search_candidates:
                        relevant_tables = [t for t in search_candidates if t in ds_tables]
                        # Expand with FKs (simple 1-hop)
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
                            # Decision: If search candidates exist, ONLY return matching. 
                            # If search candidates is empty/failed, return ALL.
                            if not relevant_tables: 
                                 # If NO GLOBAL retrieval hits, maybe return all?
                                 if not search_candidates:
                                     pass # Keep all
                                 else:
                                     # Retrieval worked but this DS has none. don't include any.
                                     ds_tables = [] 
                    
                    # Inspect Columns & FKs
                    ds_tables.sort()
                    for i, table in enumerate(ds_tables):
                        try:
                            columns = [col["name"] for col in inspector.get_columns(table)]
                            fks = [
                                ForeignKey(
                                    constrained_columns=fk["constrained_columns"],
                                    referred_table=fk["referred_table"],
                                    referred_columns=fk["referred_columns"]
                                )
                                for fk in inspector.get_foreign_keys(table)
                            ]
                            
                        
                            alias = table 
                            
                            final_table_infos.append(TableInfo(
                                name=table,
                                alias=alias,
                                columns=columns,
                                foreign_keys=fks
                            ))
                        except Exception:
                            continue
                except Exception:
                    continue
                
            
            return {
                "schema_info": SchemaInfo(tables=final_table_infos),
                "retrieved_tables": retrieved_tables_update
            }

        except Exception as e:
            logger.error(f"Node {node_name} failed: {e}")
            raise e
