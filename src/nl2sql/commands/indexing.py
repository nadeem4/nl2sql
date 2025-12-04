import sys
from typing import Dict, Any
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from nl2sql.vector_store import SchemaVectorStore
from nl2sql.router_store import DatasourceRouterStore
from nl2sql.engine_factory import make_engine

def run_indexing(profiles: Dict[str, Any], vector_store_path: str, vector_store: SchemaVectorStore) -> None:
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
        
        # Index Datasource Descriptions
        task_desc = progress.add_task("[magenta]Indexing datasource descriptions...", total=1)
        from nl2sql.settings import settings
        router_store.index_datasources(profiles, examples_path=settings.sample_questions_path)
        progress.advance(task_desc)
        
        # Index Schemas for ALL profiles
        task_schema = progress.add_task("[green]Indexing schemas...", total=len(profiles))
        
        for p in profiles.values():
            progress.update(task_schema, description=f"[green]Indexing schema: {p.id} ({p.engine})...")
            try:
                eng = make_engine(p)
                vector_store.index_schema(eng, datasource_id=p.id)
            except Exception as e:
                console.print(f"[red]Failed to index {p.id}: {e}[/red]")
            progress.advance(task_schema)
            
    console.print("[bold green]Indexing complete![/bold green]")
