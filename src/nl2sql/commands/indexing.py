import sys
from typing import Dict, Any
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from nl2sql.vector_store import SchemaVectorStore
from nl2sql.router_store import DatasourceRouterStore
from nl2sql.engine_factory import make_engine

def run_indexing(profiles: Dict[str, Any], vector_store_path: str, vector_store: SchemaVectorStore, llm_registry: Any = None) -> None:
    console = Console()
    console.print(f"[bold blue]Indexing schema to:[/bold blue] {vector_store_path}")
    
    # Initialize Router Store
    router_store = DatasourceRouterStore(persist_directory=vector_store_path)
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    ) as progress:
        
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
                console.print(f"[red]Failed to index {p.id}: {e}[/red]")
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
                console.print(f"[yellow]Warning: Could not load router LLM: {e}[/yellow]")
        
        if not llm:
             console.print("[yellow]Warning: Indexing without enrichment (No LLM provided).[/yellow]")

        router_store.index_datasources(
            profiles, 
            schemas=schema_summaries, 
            examples_path=settings.sample_questions_path,
            llm=llm
        )
        progress.advance(task_desc)
            
    console.print("[bold green]Indexing complete![/bold green]")
