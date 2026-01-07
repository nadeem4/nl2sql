import os
import yaml
import pathlib
from rich.prompt import Confirm, Prompt
from rich.panel import Panel
from nl2sql_cli.console import console, print_success, print_step, print_error
from nl2sql_cli.config import KNOWN_ADAPTERS, CORE_PACKAGE
from nl2sql_cli.commands.install import install_package
from nl2sql_cli.checks import check_package, verify_connectivity

CONFIG_DIR = pathlib.Path("configs")
DATASOURCE_CONFIG = CONFIG_DIR / "datasources.yaml"
LLM_CONFIG = CONFIG_DIR / "llm.yaml"

def _ensure_directories():
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        console.print("[dim]Created configs directory.[/dim]")

from rich.table import Table

def _configure_datasource():
    if DATASOURCE_CONFIG.exists():
        console.print(Panel("[bold]1. Datasource Configuration[/bold]", border_style="cyan"))
        try:
             with open(DATASOURCE_CONFIG, "r") as f:
                data = yaml.safe_load(f) or {}

             # Handle V2 root
             if isinstance(data, dict) and "datasources" in data:
                 data = data["datasources"]
             
             table = Table(show_header=True, header_style="bold cyan", title="Existing Datasources", box=None)
             table.add_column("ID", style="cyan")
             table.add_column("Type", style="green")
             table.add_column("Details", style="dim")
             
             # Support List or Dict
             if isinstance(data, list):
                 for d in data:
                     conn = d.get('connection', {})
                     host = conn.get('host', 'local')
                     user = conn.get('user', '')
                     details = f"{user}@{host}" if user else host
                     table.add_row(d.get('id', 'unknown'), d.get('type', d.get('engine', '?')), details)
             else:
                 # Legacy dict support
                 for k, v in data.items():
                     table.add_row(k, v.get('engine', '?'), "Legacy Config")
             
             console.print(table)
        except Exception:
             console.print("[dim]Existing configuration found (could not parse).[/dim]")
        return
        
    console.print(Panel("[bold]1. Datasource Configuration[/bold]", border_style="cyan"))
    console.print("No datasource configuration found. Let's create one.")
    
    db_type = Prompt.ask("Select Database Type", choices=["postgres", "mysql", "mssql", "sqlite"], default="postgres")
    
    config_data = {}
    profile = {}
    
    if db_type == "sqlite":
        db_path = Prompt.ask("Database Path", default="./my_database.db")
        profile = {
            "id": "my_sqlite_db",
            "type": "sqlite",
            "connection": {
                "database": db_path
            },
            "description": "Main application database"
        }
    else:
        host = Prompt.ask("Host", default="localhost")
        
        default_ports = {"postgres": "5432", "mysql": "3306", "mssql": "1433"}
        port = Prompt.ask("Port", default=default_ports.get(db_type, "5432"))
        user = Prompt.ask("Username", default="postgres" if db_type == "postgres" else "root")
        dbname = Prompt.ask("Database Name")
        
        # Password & Secrets
        password = Prompt.ask("Password", password=True)
        final_password = password
        
        if Confirm.ask("Secure this password with an Environment Variable?"):
             env_var = Prompt.ask("Environment Variable Name", default="DB_PASSWORD")
             final_password = f"${{env:{env_var}}}"
             console.print(f"[dim]Will save as: {final_password}[/dim]")
             os.environ[env_var] = password # Set it for current session so validation passes
        
        profile = {
            "id": "main_db",
            "type": db_type,
            "connection": {
                "host": host,
                "port": int(port),
                "user": user,
                "password": final_password,
                "database": dbname
            },
            "options": {} 
        }
        
        # Add driver for MSSQL default
        if db_type == "mssql":
            profile["connection"]["driver"] = "ODBC Driver 17 for SQL Server"

    # Write V2 Structure
    output_data = {
        "version": 2,
        "datasources": [profile]
    }

    with open(DATASOURCE_CONFIG, "w") as f:
        yaml.dump(output_data, f, sort_keys=False)
    
    print_success(f"Created {DATASOURCE_CONFIG}")

def _configure_llm():
    if LLM_CONFIG.exists():
        console.print(Panel("[bold]2. LLM Configuration[/bold]", border_style="magenta"))
        try:
             with open(LLM_CONFIG, "r") as f:
                data = yaml.safe_load(f) or {}
             
             table = Table(show_header=True, header_style="bold magenta", title="Existing LLMs", box=None)
             table.add_column("ID", style="magenta")
             table.add_column("Provider", style="green")
             table.add_column("Model", style="yellow")

             for k, v in data.items():
                 table.add_row(k, v.get('provider', '?'), v.get('model', '?'))
                 
             console.print(table)
        except Exception:
             console.print("[dim]Existing configuration found (could not parse).[/dim]")
        return
        
    console.print(Panel("[bold]2. LLM Configuration[/bold]", border_style="magenta"))
    console.print("No LLM configuration found. Let's configure one.")
    
    provider = Prompt.ask("Select Provider", choices=["openai", "gemini", "ollama"], default="openai")
    
    llm_data = {}
    
    if provider == "openai":
        api_key = Prompt.ask("OpenAI API Key", password=True)
        llm_data = {
            "main_llm": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key": api_key,
                "temperature": 0.0
            }
        }
    elif provider == "gemini":
        api_key = Prompt.ask("Gemini API Key", password=True)
        llm_data = {
            "main_llm": {
                "provider": "google_genai",
                "model": "gemini-1.5-pro",
                "api_key": api_key,
                "temperature": 0.0
            }
        }
    elif provider == "ollama":
        base_url = Prompt.ask("Base URL", default="http://localhost:11434")
        model = Prompt.ask("Model Name", default="llama3")
        llm_data = {
            "main_llm": {
                "provider": "ollama",
                "model": model,
                "base_url": base_url,
                "temperature": 0.0
            }
        }

    with open(LLM_CONFIG, "w") as f:
        yaml.dump(llm_data, f, sort_keys=False)
        
    print_success(f"Created {LLM_CONFIG}")

def _install_required_adapters():
    """Reads config and installs necessary adapters."""
    if not DATASOURCE_CONFIG.exists():
        return

    try:
        from nl2sql.datasources import load_configs
        try:
            # configs is List[Dict[str, Any]]
            config_list = load_configs(DATASOURCE_CONFIG)
        except Exception:
             # Fallback manual read
             with open(DATASOURCE_CONFIG, "r") as f:
                data = yaml.safe_load(f)
             
             if isinstance(data, list):
                 config_list = data
             elif isinstance(data, dict):
                 # Handle legacy root or datasources key
                 if "datasources" in data:
                     config_list = data["datasources"]
                 else:
                     config_list = list(data.values())
             else:
                 config_list = []

        required_pkgs = set()
        for config in config_list:
            if not isinstance(config, dict):
                continue
                
            # Extract engine/type
            connection = config.get("connection", {})
            engine = connection.get("type", "").lower()
            
            if not engine:
                engine = config.get("type", "").lower()

            # Map engine to package
            for name, pkg in KNOWN_ADAPTERS.items():
                if name in engine: # quick fuzzy match e.g. 'postgres' in 'postgresql'
                    required_pkgs.add(pkg)
                    break
        
        if required_pkgs:
            print_step("Checking Adapters...")
            for pkg in required_pkgs:
                import_name = pkg.replace("-", "_")
                if not check_package(import_name):
                    if Confirm.ask(f"Required adapter [bold]{pkg}[/bold] is missing. Install now?"):
                        console.print(f"[yellow]Installing {pkg}...[/yellow]")
                        install_package(pkg)
                else:
                    console.print(f"[dim]Adapter {pkg} is installed.[/dim]")
                    
    except Exception as e:
        console.print(f"[red]Failed to check adapters: {e}[/red]")

def setup_command():
    console.print("[bold cyan]NL2SQL Setup Wizard[/bold cyan]\n")
    
    _ensure_directories()
    
    # 1. Datasource
    _configure_datasource()
    
    # 2. LLM
    _configure_llm()
    
    # 3. Adapters
    _install_required_adapters()

    # 4. Connectivity Check
    print_step("Checking Database Connectivity...")
    if not verify_connectivity(print_table=True):
        console.print("[yellow]Warning: Some datasources are failing validation.[/yellow]")
        if not Confirm.ask("Continue anyway?"):
            return

    # 5. Indexing
    console.print("")
    
    from nl2sql.services.vector_store import OrchestratorVectorStore
    from nl2sql.services.llm import LLMRegistry, load_llm_config
    from nl2sql.common.settings import settings
    from nl2sql.datasources import load_configs
    from nl2sql_cli.commands.indexing import run_indexing
    
    try:
        # Load configs to pass to indexing
        configs = load_configs(pathlib.Path(settings.datasource_config_path))
        
        v_store = OrchestratorVectorStore(persist_directory=settings.vector_store_path)
        
        should_index = False
        if not v_store.is_empty():
            console.print("[yellow]Vector Store already contains data.[/yellow]")
            if Confirm.ask("Do you want to clear and re-index?"):
                should_index = True
        else:
            if Confirm.ask("Vector Store is empty. Run Schema Indexing now?"):
                should_index = True

        if should_index:
            print_step("Starting Indexer...")
            try:
                llm_cfg = load_llm_config(pathlib.Path(settings.llm_config_path))
                llm_registry = LLMRegistry(llm_cfg)
            except Exception:
                llm_registry = None

            run_indexing(configs, settings.vector_store_path, v_store, llm_registry)
            print_success("Indexing process finished.")
            
    except Exception as e:
        console.print(f"[red]Indexing setup failed: {e}[/red]")
        
    console.print("\n[bold green]Setup Complete![/bold green]")
    console.print("Try running a query: [cyan]nl2sql run \"Show me all tables\"[/cyan]")
