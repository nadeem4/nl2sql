from __future__ import annotations

import json
from typing import Dict, Optional, List
from sqlalchemy import inspect
from nl2sql.schemas import GraphState, SchemaInfo, TableInfo, ForeignKey
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.datasource_config import DatasourceProfile
from nl2sql.engine_factory import make_engine


class SchemaNode:
    """
    Retrieves schema information (tables, columns, foreign keys) based on the user query.
    
    Uses vector store for relevant table selection if available, otherwise retrieves all tables.
    Also handles assigning aliases to tables and columns for the planner.
    """

    def __init__(self, profile: DatasourceProfile, vector_store: Optional[SchemaVectorStore] = None):
        """
        Initializes the SchemaNode.

        Args:
            profile: Database connection profile.
            vector_store: Optional vector store for schema retrieval.
        """
        self.profile = profile
        self.vector_store = vector_store

    def __call__(self, state: GraphState) -> GraphState:
        """
        Executes the schema retrieval step.

        Args:
            state: The current graph state.

        Returns:
            The updated graph state with schema information.
        """
        engine = make_engine(self.profile)
        inspector = inspect(engine)
        all_tables = inspector.get_table_names()
        
        if self.vector_store:
            try:
                search_query = state.user_query
                if state.validation.get("intent"):
                    try:
                        intent = state.validation["intent"]
                        if isinstance(intent, str):
                            intent = json.loads(intent)
                            
                        extras = " ".join(intent.get("entities", []) + intent.get("keywords", []))
                        if extras:
                            search_query = f"{state.user_query} {extras}"
                    except Exception:
                        pass

                retrieved = self.vector_store.retrieve(search_query)
                state.retrieved_tables = retrieved
                
                tables = [t for t in retrieved if t in all_tables]

                if tables:
                    expanded_tables = set(tables)
                    for table in tables:
                        fks = inspector.get_foreign_keys(table)
                        for fk in fks:
                            ref_table = fk.get("referred_table")
                            if ref_table and ref_table in all_tables:
                                expanded_tables.add(ref_table)
                    tables = list(expanded_tables)

                if not tables:
                    tables = all_tables
            except Exception:
                tables = all_tables
        else:
            tables = all_tables

        sorted_tables = sorted(tables)
        table_infos = []
        
        for i, table in enumerate(sorted_tables):
            alias = f"t{i+1}"
            columns = []
            fks = []
            
            try:
                columns = [f"{alias}.{col['name']}" for col in inspector.get_columns(table)]
                
                for fk in inspector.get_foreign_keys(table):
                    if not fk.get("referred_table"):
                        continue
                    col = fk.get("constrained_columns", [None])[0]
                    ref_table = fk.get("referred_table")
                    ref_col = fk.get("referred_columns", [None])[0]
                    
                    if col and ref_table and ref_col:
                        fks.append(ForeignKey(
                            column=col,
                            referred_table=ref_table,
                            referred_column=ref_col
                        ))
            except Exception:
                pass
            
            table_infos.append(TableInfo(
                name=table,
                alias=alias,
                columns=columns,
                foreign_keys=fks
            ))
        
        state.schema_info = SchemaInfo(tables=table_infos)

        return state
