import sys
from typing import Dict, Any
from nl2sql.reporting import ConsolePresenter
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.datasources import DatasourceRegistry, DatasourceProfile

def run_indexing(
    profiles: Dict[str, DatasourceProfile], 
    vector_store_path: str, 
    vector_store: OrchestratorVectorStore, 
    llm_registry: Any = None
) -> None:
    """Runs the indexing process for schemas and examples.

    Args:
        profiles (Dict[str, DatasourceProfile]): Dictionary of datasource profiles.
        vector_store_path (str): Path to the vector store.
        vector_store (OrchestratorVectorStore): The vector store instance.
        llm_registry (Any, optional): Registry of LLMs for enrichment.
    """
    presenter = ConsolePresenter()
    presenter.print_indexing_start(vector_store_path)
    
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
        
        if not llm_registry:
             presenter.print_warning("Indexing examples without enrichment (No LLM Registry provided).")
        
        vector_store.index_examples(
            settings.sample_questions_path,
            llm_registry=llm_registry
        )
        progress.advance(task_desc)
            
    presenter.print_indexing_complete()
