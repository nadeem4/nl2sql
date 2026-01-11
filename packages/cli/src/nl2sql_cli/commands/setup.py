
import os
import pathlib
from rich.panel import Panel
from InquirerPy import inquirer
from InquirerPy.validator import NumberValidator

from nl2sql_cli.common.decorators import handle_cli_errors
from nl2sql_cli.console import console, print_success, print_step
from nl2sql_cli.config import KNOWN_ADAPTERS
from nl2sql_cli.commands.install import install_package
from nl2sql_cli.checks import check_package, verify_connectivity

from nl2sql.configs import ConfigManager
from nl2sql.configs import (
    DatasourceConfig, 
    DatasourceFileConfig,
    ConnectionConfig, 
    LLMFileConfig,
    AgentConfig, 
    PolicyFileConfig, 
    RolePolicy
)
from nl2sql_cli.demo import DemoManager

# Resolve Project Root (Robust to CWD)
try:
    PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[5]
    if not (PROJECT_ROOT / "configs").exists():
        PROJECT_ROOT = pathlib.Path.cwd()
except Exception:
    PROJECT_ROOT = pathlib.Path.cwd()

CONFIG_DIR = PROJECT_ROOT / "configs"
DATASOURCE_CONFIG = CONFIG_DIR / "datasources.yaml"
LLM_CONFIG = CONFIG_DIR / "llm.yaml"
POLICIES_CONFIG = CONFIG_DIR / "policies.json"


from typing import Optional
from nl2sql.configs import DatasourceConfig, ConnectionConfig

def _configure_datasource(config_manager: ConfigManager):
    """Interactively configures datasources."""
    
    if DATASOURCE_CONFIG.exists():
        console.print(Panel("[bold]1. Datasource Configuration[/bold]", border_style="cyan"))
        console.print("[dim]Existing configuration found.[/dim]")
        return
        
    console.print(Panel("[bold]1. Datasource Configuration[/bold]", border_style="cyan"))
    console.print("No datasource configuration found. Let's create one.")
    
    db_type = inquirer.select(
        message="Select Database Type:",
        choices=["postgres", "mysql", "mssql", "sqlite"],
        default="postgres"
    ).execute()
    
    ds_config = None
    
    if db_type == "sqlite":
        db_path = inquirer.text(message="Database Path:", default="./my_database.db").execute()
        conn = ConnectionConfig(type="sqlite", database=db_path)
        ds_config = DatasourceConfig(
            id="my_sqlite_db",
            description="Main application database",
            connection=conn
        )
    else:
        host = inquirer.text(message="Host:", default="localhost").execute()
        default_ports = {"postgres": "5432", "mysql": "3306", "mssql": "1433"}
        port = inquirer.text(
             message="Port:",
             default=default_ports.get(db_type, "5432"),
             validate=NumberValidator()
        ).execute()

        user = inquirer.text(
            message="Username:",
            default="postgres" if db_type == "postgres" else "root"
        ).execute()

        dbname = inquirer.text(message="Database Name:").execute()
        
        # Password & Secrets
        password = inquirer.secret(message="Password:").execute()
        final_password = password
        
        if inquirer.confirm(message="Secure this password with an Environment Variable?", default=True).execute():
             env_var = inquirer.text(message="Environment Variable Name:", default="DB_PASSWORD").execute()
             final_password = f"${{env:{env_var}}}"
             console.print(f"[dim]Will save as: {final_password}[/dim]")
             os.environ[env_var] = password # Set it for current session so validation passes
        
        conn_args = {
            "type": db_type,
            "host": host,
            "port": int(port),
            "user": user,
            "password": final_password,
            "database": dbname
        }
        
        if db_type == "mssql":
            conn_args["driver"] = "ODBC Driver 17 for SQL Server"
            
        conn = ConnectionConfig(**conn_args)
        
        ds_config = DatasourceConfig(
            id="main_db", 
            connection=conn,
            options={}
        )

    # Write using Manager
    # Write using Generator
    ds_configs = [ds_config]
    if ds_configs:
        console.print(f"[green]Generated configuration for {len(ds_configs)} datasources.[/green]")
        file_config = DatasourceFileConfig(datasources=ds_configs)
        content = DatasourceGenerator.generate(file_config)
        _write_config_file(DATASOURCE_CONFIG, content)


from nl2sql.configs import LLMFileConfig, AgentConfig
from nl2sql_cli.generators.llm import LLMGenerator


def _configure_llm(config_manager: ConfigManager, api_key: Optional[str] = None):
    """Interactively configures LLM."""
    if LLM_CONFIG.exists():
        console.print(Panel("[bold]2. LLM Configuration[/bold]", border_style="magenta"))
        console.print("[dim]Existing configuration found.[/dim]")
        return
        
    console.print(Panel("[bold]2. LLM Configuration[/bold]", border_style="magenta"))
    
    if api_key:
         console.print("[green]API Key provided via CLI. Creating default OpenAI configuration.[/green]")
         default_agent = AgentConfig(
            provider="openai",
            model="gpt-4o",
            api_key="${env:OPENAI_API_KEY}"
         )
         llm_config = LLMFileConfig(default=default_agent)
         content = LLMGenerator.generate(llm_config)
         _write_config_file(LLM_CONFIG, content)
         return

    console.print("No LLM configuration found. Let's configure one.")
    
    provider = inquirer.select(
        message="Select Provider:",
        choices=["openai", "gemini", "ollama"],
        default="openai"
    ).execute()
    
    default_agent = None
    
    if provider == "openai":
        api_key = inquirer.secret(message="OpenAI API Key:").execute()
        default_agent = AgentConfig(
            provider="openai",
            model="gpt-4o",
            api_key=api_key
        )
    elif provider == "gemini":
        api_key = inquirer.secret(message="Gemini API Key:").execute()
        default_agent = AgentConfig(
            provider="google_genai",
            model="gemini-1.5-pro",
            api_key=api_key
        )
    elif provider == "ollama":
        base_url = inquirer.text(message="Base URL:", default="http://localhost:11434").execute()
        model = inquirer.text(message="Model Name:", default="llama3").execute()
        default_agent = AgentConfig(
            provider="ollama",
            model=model,
            base_url=base_url
        )

    llm_config = LLMFileConfig(default=default_agent)
    content = LLMGenerator.generate(llm_config)
    _write_config_file(LLM_CONFIG, content)

from nl2sql.configs import PolicyFileConfig, RolePolicy

def _configure_policies(config_manager: ConfigManager):
    """Generates default policies."""
    if POLICIES_CONFIG.exists():
        console.print(Panel("[bold]3. Policy Configuration[/bold]", border_style="yellow"))
        console.print("[dim]Existing policies configuration found.[/dim]")
        return

    console.print(Panel("[bold]3. Policy Configuration[/bold]", border_style="yellow"))
    console.print("Generating default RBAC policies...")
    
    admin_policy = RolePolicy(
        description="System Administrator",
        role="admin",
        allowed_datasources=["*"],
        allowed_tables=["*"]
    )
    
    policy_config = PolicyFileConfig(roles={"admin": admin_policy})
    
    content = PolicyGenerator.generate(policy_config)
    _write_config_file(POLICIES_CONFIG, content)



from nl2sql_cli.generators.env import EnvFileGenerator
from nl2sql_cli.generators.datasources import DatasourceGenerator
from nl2sql_cli.generators.llm import LLMGenerator
from nl2sql_cli.generators.policies import PolicyGenerator

def _write_config_file(path: pathlib.Path, content: str):
    """Helper to write generator output to file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print_success(f"Created {path}")
    except Exception as e:
        console.print(f"[red]Failed to write {path.name}: {e}[/red]")


def _configure_env_file(env: str, api_key: Optional[str] = None):
    """Creates the .env.{env} file using the Universal Environment Protocol."""
    target_file = PROJECT_ROOT / f".env.{env}"
    
    if target_file.exists():
        console.print(Panel(f"[bold]Environment Configuration ({env})[/bold]", border_style="blue"))
        console.print(f"[dim]Existing {target_file.name} found.[/dim]")
        return
    
    console.print(Panel(f"[bold]Environment Configuration ({env})[/bold]", border_style="blue"))
    console.print(f"Creating explicit configuration file: {target_file.name}")
    
    # Generate Content
    secrets = {}
    if api_key:
        secrets["OPENAI_API_KEY"] = api_key
        
    content = EnvFileGenerator.generate(env, secrets=secrets)

    try:
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(content)
        print_success(f"Created {target_file}")
    except Exception as e:
        console.print(f"[red]Failed to write env file: {e}[/red]")

def _install_required_adapters(config_manager: ConfigManager):
    """Reads config using ConfigManager and installs necessary adapters."""
    if not DATASOURCE_CONFIG.exists():
        return

    try:
        # Use ConfigManager to load standardized objects
        # load_datasources returns List[Dict]
        configs = config_manager.load_datasources()
        
        required_pkgs = set()
        for config in configs:
            connection = config.get("connection", {})
            engine = connection.get("type", "").lower() or config.get("type", "").lower()

            for name, pkg in KNOWN_ADAPTERS.items():
                if name in engine:
                    required_pkgs.add(pkg)
                    break
        
        if required_pkgs:
            print_step("Checking Adapters...")
            for pkg in required_pkgs:
                import_name = pkg.replace("-", "_")
                if not check_package(import_name):
                    if inquirer.confirm(message=f"Required adapter {pkg} is missing. Install now?", default=True).execute():
                        console.print(f"[yellow]Installing {pkg}...[/yellow]")
                        install_package(pkg)
                else:
                    console.print(f"[dim]Adapter {pkg} is installed.[/dim]")
                    
    except Exception as e:
        console.print(f"[red]Failed to check adapters: {e}[/red]")


@handle_cli_errors
def setup_command(demo: bool = False, lite: bool = True, docker: bool = False, api_key: Optional[str] = None):
    
    # Instantiate Managers
    config_manager = ConfigManager(PROJECT_ROOT)
    demo_manager = DemoManager(console, PROJECT_ROOT)

    if demo:
        console.print(Panel("[bold green]Setting up Demo Environment...[/bold green]", border_style="green"))
        
        if lite:
            demo_manager.setup_lite(api_key=api_key)
        elif docker:
            docker_dir = demo_manager.setup_docker(api_key=api_key)
            if inquirer.confirm(message="Start Docker containers now?", default=True).execute():
                demo_manager.start_docker_containers(docker_dir)
            if inquirer.confirm(message="Apply secrets to local environment (.env.demo)?", default=True).execute():
                demo_manager.copy_docker_env_to_root(docker_dir)
                
            console.print(Panel(f"""[bold yellow]Next Steps:[/bold yellow]
                    1. [bold]Verify & Index[/bold]:
                    Once database containers are healthy (~30s), run:
                    [cyan]nl2sql --env demo index[/cyan]
                    """, title="Docker Instructions", border_style="yellow")
                )
        
        if not docker:
            print_step("Indexing Demo Environment...")
            demo_manager.index_demo_data()
            console.print("Run: [cyan]nl2sql --env demo run \"Show me broken machines in Austin\"[/cyan]")
            
        return

    # --- Standard Setup Wizard ---
    console.print("[bold cyan]NL2SQL Setup Wizard[/bold cyan]\n")
    
    config_manager.ensure_config_dirs()
    
    # 1. Environment File
    _configure_env_file("dev", api_key=api_key)
    
    # 2. Datasource
    _configure_datasource(config_manager)
    
    # 2. LLM
    _configure_llm(config_manager, api_key=api_key)

    # 3. Policies
    _configure_policies(config_manager)
    
    # 4. Adapters
    _install_required_adapters(config_manager)

    # 5. Connectivity Check
    print_step("Checking Database Connectivity...")
    if not verify_connectivity(print_table=True):
        console.print("[yellow]Warning: Some datasources are failing validation.[/yellow]")
        if not inquirer.confirm(message="Continue anyway?", default=False).execute():
            return

    # 6. Indexing Prompt
    console.print("")
    from nl2sql.services.vector_store import OrchestratorVectorStore
    from nl2sql.common.settings import settings
    from nl2sql_cli.commands.indexing import run_indexing
    from nl2sql.services.llm import LLMRegistry

    try:
        v_store = OrchestratorVectorStore(persist_directory=settings.vector_store_path)
        should_index = False
        
        if not v_store.is_empty():
            console.print("[yellow]Vector Store already contains data.[/yellow]")
            if inquirer.confirm(message="Do you want to clear and re-index?", default=False).execute():
                should_index = True
        else:
             if inquirer.confirm(message="Vector Store is empty. Run Schema Indexing now?", default=True).execute():
                should_index = True

        if should_index:
            print_step("Starting Indexer...")
            
            # Use ConfigManager to load!
            configs = config_manager.load_datasources()
            
            llm_registry = None
            try:
                llm_cfg = config_manager.load_llm()
                llm_registry = LLMRegistry(llm_cfg)
            except Exception:
                pass
            
            run_indexing(configs, settings.vector_store_path, v_store, llm_registry)
            print_success("Indexing process finished.")
            
    except Exception as e:
        console.print(f"[red]Indexing setup failed: {e}[/red]")
        
    console.print("\n[bold green]Setup Complete![/bold green]")
    console.print("Try running a query: [cyan]nl2sql run \"Show me all tables\"[/cyan]")
