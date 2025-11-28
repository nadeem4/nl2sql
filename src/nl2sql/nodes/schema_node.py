
from __future__ import annotations

import json
from typing import Dict, Optional, List
from sqlalchemy import inspect
from nl2sql.schemas import GraphState
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.datasource_config import DatasourceProfile
from nl2sql.engine_factory import make_engine

class SchemaNode:
    """
    Retrieves schema information (tables, columns, foreign keys) based on the user query.
    Uses vector store for relevant table selection if available, otherwise retrieves all tables.
    """

    def __init__(self, profile: DatasourceProfile, vector_store: Optional[SchemaVectorStore] = None):
        self.profile = profile
        self.vector_store = vector_store

    def __call__(self, state: GraphState) -> GraphState:
        engine = make_engine(self.profile)
        inspector = inspect(engine)
        all_tables = inspector.get_table_names()
        
        if self.vector_store:
            try:
                retrieved = self.vector_store.retrieve(state.user_query)
                state.retrieved_tables = retrieved
                # Filter to ensure they exist in current DB
                tables = [t for t in retrieved if t in all_tables]
                # Fallback if retrieval returns nothing relevant
                if not tables:
                    tables = all_tables
            except Exception:
                tables = all_tables
        else:
            tables = all_tables

        state.validation["schema_tables"] = ", ".join(sorted(tables))

        try:
            columns_map = {
                table: [col["name"] for col in inspector.get_columns(table)]
                for table in tables
            }
            state.validation["schema_columns"] = json.dumps(columns_map)
            
            fk_map = {}
            for table in tables:
                fks = []
                for fk in inspector.get_foreign_keys(table):
                    if not fk.get("referred_table"):
                        continue
                    col = fk.get("constrained_columns", [None])[0]
                    ref_table = fk.get("referred_table")
                    ref_col = fk.get("referred_columns", [None])[0]
                    fks.append({"column": col, "reftable": ref_table, "refcolumn": ref_col})
                if fks:
                    fk_map[table] = fks
            if fk_map:
                state.validation["schema_fks"] = json.dumps(fk_map)
        except Exception:
            pass
            
        return state
