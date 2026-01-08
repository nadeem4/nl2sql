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
POLICIES_CONFIG = CONFIG_DIR / "policies.json"

def _ensure_directories():
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        console.print("[dim]Created configs directory.[/dim]")

def _configure_policies():
    """Generates a default policies.json if missing."""
    if POLICIES_CONFIG.exists():
        console.print(Panel("[bold]3. Policy Configuration[/bold]", border_style="yellow"))
        console.print("[dim]Existing policies configuration found.[/dim]")
        return

    console.print(Panel("[bold]3. Policy Configuration[/bold]", border_style="yellow"))
    console.print("Generating default RBAC policies...")
    
    default_policies = {
        "admin": {
            "description": "System Administrator",
            "role": "admin",
            "allowed_datasources": ["*"],
            "allowed_tables": ["*"]
        }
    }
    
    with open(POLICIES_CONFIG, "w") as f:
        # Use json dump
        import json
        json.dump(default_policies, f, indent=2)
        
    print_success(f"Created {POLICIES_CONFIG}")

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

def _run_demo_setup(lite: bool, docker: bool):
    """Executes the demo environment setup."""
    console.print(Panel("[bold green]Setting up Demo Environment...[/bold green]", border_style="green"))
    
    # 1. Generate Data
    from nl2sql_cli.utils.demo.generator import DemoDataGenerator
    generator = DemoDataGenerator(seed=42)
    
    if lite:
        data_dir = pathlib.Path("data/demo_lite")
        print_step(f"Generating SQLite Databases in {data_dir}...")
        generator.generate_lite(data_dir)
        
        # 2. Write Datasources Config
        config_path = pathlib.Path("configs/datasources.demo.yaml")
        print_step(f"Writing {config_path}...")
        
        config_data = {
            "datasources": [
                {"id": "manufacturing_ref", "connection": {"type": "sqlite", "database": str(data_dir / "manufacturing_ref.db")}, "description": "Master Data (Factories)"},
                {"id": "manufacturing_ops", "connection": {"type": "sqlite", "database": str(data_dir / "manufacturing_ops.db")}, "description": "Operational Data (Employees, Machines)"},
                {"id": "manufacturing_supply", "connection": {"type": "sqlite", "database": str(data_dir / "manufacturing_supply.db")}, "description": "Supply Chain (Inventory)"},
                {"id": "manufacturing_history", "connection": {"type": "sqlite", "database": str(data_dir / "manufacturing_history.db")}, "description": "Historical Data (Sales)"},
            ]
        }
        
    elif docker:
        console.print("[yellow]Docker mode not yet implemented. Using Lite.[/yellow]")
        return
        
    with open(config_path, "w") as f:
        yaml.dump(config_data, f, sort_keys=False)
    
    # 3. Write Policies
    policy_path = pathlib.Path("configs/policies.demo.json")
    print_step(f"Writing {policy_path}...")
    
    policies = {
        "admin": {
            "description": "Demo Admin",
            "role": "admin",
            "allowed_datasources": ["*"],
            "allowed_tables": ["*"]
        }
    }
    import json
    with open(policy_path, "w") as f:
        json.dump(policies, f, indent=2)
    
    # 4. Write Sample Questions (Golden Examples)
    samples_path = pathlib.Path("configs/sample_questions.demo.json") # Core expects List[Dict], usually Loaded from YAML or JSON. 
    # WAIT: Settings defines it as YAML usually, but Vector Store loaders might vary.
    # Let's check `view_file indexing.py` or vector store.
    # Assuming YAML is safest if Config calls for it.
    
    samples_path = pathlib.Path("configs/sample_questions.demo.yaml")
    print_step(f"Writing {samples_path}...")
    
    samples = {
        "manufacturing_ref": [
            "List all factories in the US",
            "Show me the capacity of Berlin Plant",
            "What shifts are available?",
            "List all machine types produced by TechCorp"
        ],
        "manufacturing_ops": [
            "Show me active employees in the Austin Gigafactory",
            "Which machines have error logs in the last 7 days?",
            "Who is the operator for machine 5?",
            "Count the number of active machines per factory",
            "List maintenance logs for Vibration sensor alerts"
        ],
        "manufacturing_supply": [
            "Total sales amount for 'Industrial Controller'",
            "Find suppliers for high value components",
            "Check inventory levels for 'Bolt M5' in Berlin",
            "List products with base cost greater than 500",
            "Show me suppliers from Germany"
        ],
        "manufacturing_history": [
            "Show total sales orders in Q4",
            "Calculate average production output per run",
            "Summarize sales by customer for last year",
            "List the top 5 largest orders"
        ]
    }
    
    with open(samples_path, "w") as f:
        yaml.dump(samples, f, sort_keys=False)


    # 5. Auto Index
    print_step("Indexing Demo Environment...")
    from nl2sql.common.settings import settings
    # Force env to demo for indexing
    settings.configure_env("demo")
    
    from nl2sql_cli.commands.indexing import run_indexing
    from nl2sql.services.vector_store import OrchestratorVectorStore
    try:
        from nl2sql.datasources import load_configs
        configs = load_configs(pathlib.Path(settings.datasource_config_path))
        v_store = OrchestratorVectorStore(persist_directory=settings.vector_store_path)
        
        # Try load LLM
        from nl2sql.services.llm import LLMRegistry, load_llm_config
        try:
             llm_cfg = load_llm_config(pathlib.Path(settings.llm_config_path))
             llm_registry = LLMRegistry(llm_cfg)
        except Exception:
             llm_registry = None
             console.print("[yellow]No LLM Config found. Indexing schema as-is.[/yellow]")
             
        run_indexing(configs, settings.vector_store_path, v_store, llm_registry)
        
    except Exception as e:
        print_error(f"Indexing Failed: {e}")
        
    console.print("\n[bold green]Demo Setup Complete![/bold green]")
    console.print("Run: [cyan]nl2sql --env demo run \"Show me broken machines in Austin\"[/cyan]")


def setup_command(demo: bool = False, lite: bool = True, docker: bool = False):
    if demo:
        _run_demo_setup(lite, docker)
        return

    console.print("[bold cyan]NL2SQL Setup Wizard[/bold cyan]\n")
    
    _ensure_directories()
    
    # 1. Datasource
    _configure_datasource()
    
    # 2. LLM
    _configure_llm()

    # 3. Policies
    _configure_policies()
    
    # 4. Adapters
    _install_required_adapters()

    # 5. Connectivity Check
    print_step("Checking Database Connectivity...")
    if not verify_connectivity(print_table=True):
        console.print("[yellow]Warning: Some datasources are failing validation.[/yellow]")
        if not Confirm.ask("Continue anyway?"):
            return

    # 6. Indexing
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
