
from __future__ import annotations

import json
from typing import Dict, Optional, List
from sqlalchemy import inspect
from nl2sql.schemas import GraphState, SchemaInfo
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

        columns_map = {}
        fk_map = {}
        try:
            columns_map = {
                table: [col["name"] for col in inspector.get_columns(table)]
                for table in tables
            }
            
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
        except Exception:
            pass
            
        state.schema_info = SchemaInfo(
            tables=sorted(tables),
            columns=columns_map,
            foreign_keys=fk_map
        )

        return state
