import sys
from typing import Dict, Any
from nl2sql.reporting import ConsolePresenter
from nl2sql.services.vector_store import OrchestratorVectorStore
from nl2sql.datasources import DatasourceRegistry

def run_indexing(
    configs: Any, # List[Dict[str, Any]]
    vector_store_path: str, 
    vector_store: OrchestratorVectorStore, 
    llm_registry: Any = None
) -> None:
    """Runs the indexing process for schemas and examples.

    Args:
        configs (List[Dict[str, Any]]): List of datasource configuration dicts.
        vector_store_path (str): Path to the vector store.
        vector_store (OrchestratorVectorStore): The vector store instance.
        llm_registry (Any, optional): Registry of LLMs for enrichment.
    """
    presenter = ConsolePresenter()
    presenter.print_indexing_start(vector_store_path)
    
    # Registry now eager loads adapters from configs
    registry = DatasourceRegistry(configs)
    
    # Get all active adapters
    adapters = registry.list_adapters()

    with presenter.create_progress() as progress:
        
        # Clear existing data
        task_clear = progress.add_task("[cyan]Clearing existing data...", total=1)
        vector_store.clear()
        progress.advance(task_clear)
        
        # Index Schemas for ALL active adapters
        task_schema = progress.add_task("[green]Indexing schemas...", total=len(adapters))
        
        for adapter in adapters:
            ds_id = adapter.datasource_id
            progress.update(task_schema, description=f"[green]Indexing schema: {ds_id}...")
            try:
                vector_store.index_schema(adapter, datasource_id=ds_id)
            except Exception as e:
                presenter.print_indexing_error(ds_id, str(e))
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
