import sys
from typing import Dict, Any
from nl2sql.reporting import ConsolePresenter
from nl2sql.vector_store import SchemaVectorStore
from nl2sql.router_store import DatasourceRouterStore
from nl2sql.engine_factory import make_engine

def run_indexing(profiles: Dict[str, Any], vector_store_path: str, vector_store: SchemaVectorStore, llm_registry: Any = None) -> None:
    presenter = ConsolePresenter()
    presenter.print_indexing_start(vector_store_path)
    
    # Initialize Router Store
    router_store = DatasourceRouterStore(persist_directory=vector_store_path)
    
    with presenter.create_progress() as progress:
        
        # Clear existing data
        task_clear = progress.add_task("[cyan]Clearing existing data...", total=2)
        vector_store.clear()
        progress.advance(task_clear)
        router_store.clear()
        progress.advance(task_clear)
        
        # Index Schemas for ALL profiles and collect summaries
        task_schema = progress.add_task("[green]Indexing schemas...", total=len(profiles))
        schema_summaries = {}
        
        for p in profiles.values():
            progress.update(task_schema, description=f"[green]Indexing schema: {p.id} ({p.engine})...")
            try:
                eng = make_engine(p)
                tables = vector_store.index_schema(eng, datasource_id=p.id)
                schema_summaries[p.id] = tables
            except Exception as e:
                presenter.print_indexing_error(p.id, str(e))
            progress.advance(task_schema)
            
        # Index Datasource Descriptions AND Summaries
        task_desc = progress.add_task("[magenta]Indexing router info (with enrichment)...", total=1)
        from nl2sql.settings import settings
        
        # Get LLM for enrichment
        llm = None
        if llm_registry:
            try:
                llm = llm_registry.router_llm()
            except Exception as e:
                presenter.print_warning(f"Could not load router LLM: {e}")
        
        if not llm:
             presenter.print_warning("Indexing without enrichment (No LLM provided).")

        router_store.index_datasources(
            profiles, 
            schemas=schema_summaries, 
            examples_path=settings.sample_questions_path,
            llm=llm
        )
        progress.advance(task_desc)
            
    presenter.print_indexing_complete()
