
import os
import pathlib
from rich.markup import escape
from rich.panel import Panel
from InquirerPy import inquirer
from InquirerPy.validator import NumberValidator

from nl2sql.cli.common.decorators import handle_cli_errors
from nl2sql.cli.console import console, print_success, print_step
from nl2sql.cli.config import ADAPTER_DRIVERS, KNOWN_ADAPTERS
from nl2sql.cli.commands.install import install_package
from nl2sql.cli.checks import check_package, verify_connectivity

from nl2sql.common.logger import get_logger
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
from nl2sql.cli.demo import DemoManager

logger = get_logger(__name__)

# The CLI writes where it is invoked. Do not resolve this from __file__:
# that walks up out of the installed module and, in a source checkout,
# lands on the repo root instead of the user's working directory.
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
             console.print(f"[dim]Will save as: {escape(str(final_password))}[/dim]")
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
from nl2sql.cli.generators.llm import LLMGenerator


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
    
    # Only providers the engine can actually serve are offered here; anything
    # else lets setup succeed and then fails on the first query.
    provider = inquirer.select(
        message="Select Provider:",
        choices=["openai", "openrouter"],
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
    elif provider == "openrouter":
        console.print(
            "[dim]OpenRouter is an OpenAI-compatible gateway: one key reaches "
            "Anthropic, Google, Meta and hundreds of other models.[/dim]"
        )
        api_key = inquirer.secret(message="OpenRouter API Key:").execute()
        model = inquirer.text(
            message="OpenRouter model identifier (e.g. anthropic/claude-sonnet-4.5):",
            default="anthropic/claude-sonnet-4.5"
        ).execute()
        env_var = "OPENROUTER_API_KEY"
        os.environ[env_var] = api_key  # available to the rest of this session
        default_agent = AgentConfig(
            provider="openrouter",
            model=model,
            api_key=f"${{env:{env_var}}}"
        )
        console.print(f"[dim]Will save the key as: ${{env:{env_var}}}[/dim]")
        console.print(
            "[yellow]Note:[/yellow] embeddings still go through OpenAI, so "
            "[cyan]nl2sql index[/cyan] needs OPENAI_API_KEY as well."
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



from nl2sql.cli.generators.env import EnvFileGenerator
from nl2sql.cli.generators.datasources import DatasourceGenerator
from nl2sql.cli.generators.llm import LLMGenerator
from nl2sql.cli.generators.policies import PolicyGenerator

def _write_config_file(path: pathlib.Path, content: str):
    """Helper to write generator output to file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print_success(f"Created {path}")
    except Exception as e:
        console.print(f"[red]Failed to write {escape(str(path.name))}: {escape(str(e))}[/red]")


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
        console.print(f"[red]Failed to write env file: {escape(str(e))}[/red]")

def _install_required_adapters(config_manager: ConfigManager):
    """Reads config using ConfigManager and installs necessary adapters."""
    if not DATASOURCE_CONFIG.exists():
        return

    try:
        # Use ConfigManager to load standardized objects
        # load_datasources returns List[Dict]
        configs = config_manager.load_datasources()
        
        required = set()
        for config in configs:
            connection = config.get("connection", {})
            engine = connection.get("type", "").lower() or config.get("type", "").lower()

            for name, pkg in KNOWN_ADAPTERS.items():
                if name in engine:
                    required.add((name, pkg))
                    break
        
        if required:
            print_step("Checking Adapters...")
            for name, pkg in sorted(required):
                # The adapter ships with nl2sql; the extra supplies the driver.
                if not check_package(ADAPTER_DRIVERS[name]):
                    if inquirer.confirm(message=f"Required adapter {pkg} is missing. Install now?", default=True).execute():
                        console.print(f"[yellow]Installing {escape(pkg)}...[/yellow]")
                        install_package(pkg)
                else:
                    console.print(f"[dim]Adapter {escape(pkg)} is installed.[/dim]")
                    
    except Exception as e:
        console.print(f"[red]Failed to check adapters: {escape(str(e))}[/red]")


def _run_indexing_step():
    """Offers schema indexing and runs it through a context built from config paths."""
    from nl2sql.common.settings import settings
    from nl2sql.context import NL2SQLContext
    from nl2sql.indexing.vector_store import VectorStore
    from nl2sql.cli.commands.indexing import run_indexing

    try:
        v_store = VectorStore(
            collection_name=settings.vector_store_collection_name,
            persist_directory=str(PROJECT_ROOT / settings.vector_store_path),
        )
        should_index = False

        if not v_store.is_empty():
            console.print("[yellow]Vector Store already contains data.[/yellow]")
            if inquirer.confirm(message="Do you want to clear and re-index?", default=False).execute():
                should_index = True
        else:
            if inquirer.confirm(message="Vector Store is empty. Run Schema Indexing now?", default=True).execute():
                should_index = True

        if not should_index:
            return

        print_step("Starting Indexer...")

        # NL2SQLContext builds the datasource, LLM and vector store registries
        # itself; it takes config *paths*, not pre-built registries.
        ctx = NL2SQLContext(
            ds_config_path=PROJECT_ROOT / settings.datasource_config_path,
            secrets_config_path=PROJECT_ROOT / settings.secrets_config_path,
            llm_config_path=PROJECT_ROOT / settings.llm_config_path,
            vector_store_path=PROJECT_ROOT / settings.vector_store_path,
            policies_config_path=PROJECT_ROOT / settings.policies_config_path,
        )

        run_indexing(ctx)
        print_success("Indexing process finished.")

    except Exception as e:
        logger.debug("Indexing setup failed", exc_info=True)
        console.print(f"[red]Indexing setup failed: {escape(str(e))}[/red]")


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
                
            console.print(Panel(f"""[bold yellow]Next Steps:[/bold yellow]
                    1. [bold]Verify & Index[/bold]:
                    Once database containers are healthy (~30s), run:
                    [cyan]nl2sql --env demo index[/cyan]

                    2. [bold]API[/bold]: the 'app' container serves the REST API on
                    [cyan]http://localhost:8000[/cyan].

                    3. [bold]MSSQL[/bold] is opt-in:
                    [cyan]docker compose -f docker-compose.demo.yml --profile mssql up -d[/cyan]
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
    _run_indexing_step()

    console.print("\n[bold green]Setup Complete![/bold green]")
    console.print("Try running a query: [cyan]nl2sql run \"Show me all tables\"[/cyan]")
