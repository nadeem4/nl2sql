import sys
from typing import Dict, Any
from nl2sql.reporting import ConsolePresenter
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.datasources import DatasourceRegistry, DatasourceProfile

def run_indexing(profiles: Dict[str, Any], vector_store_path: str, vector_store: OrchestratorVectorStore, llm_registry: Any = None) -> None:
    presenter = ConsolePresenter()
    presenter.print_indexing_start(vector_store_path)
    
    # Initialize Registry
    # Note: In CLI context we assume profiles are dicts or objects. 
    # If they are dicts, we might need to convert. 
    # But usually profiles passed here are from config loader.
    registry = DatasourceRegistry(profiles)

    with presenter.create_progress() as progress:
        
        # Clear existing data
        task_clear = progress.add_task("[cyan]Clearing existing data...", total=1)
        vector_store.clear()
        progress.advance(task_clear)
        
        # Index Schemas for ALL profiles and collect summaries
        task_schema = progress.add_task("[green]Indexing schemas...", total=len(profiles))
        
        for p in profiles.values():
            progress.update(task_schema, description=f"[green]Indexing schema: {p.id} ({p.engine})...")
            try:
                adapter = registry.get_adapter(p.id)
                vector_store.index_schema(adapter, datasource_id=p.id)
            except Exception as e:
                presenter.print_indexing_error(p.id, str(e))
            progress.advance(task_schema)
            
        # Index Examples (with enrichment if LLM available)
        task_desc = progress.add_task("[magenta]Indexing examples...", total=1)
        from nl2sql.common.settings import settings
        
        llm = None
        if llm_registry:
            try:
                # Reuse intent_classifier_llm for enrichment prompts
                llm = llm_registry.intent_classifier_llm()
            except Exception as e:
                presenter.print_warning(f"Could not load LLM for enrichment: {e}")
        
        if not llm:
             presenter.print_warning("Indexing examples without enrichment (No LLM provided).")
        
        vector_store.index_examples(
            settings.sample_questions_path,
            llm=llm
        )
        progress.advance(task_desc)
            
    presenter.print_indexing_complete()
