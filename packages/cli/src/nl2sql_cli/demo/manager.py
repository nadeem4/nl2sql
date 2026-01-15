from typing import Dict, Any, Optional
import pathlib
import subprocess
import yaml
from rich.console import Console
from nl2sql.configs import (
    ConfigManager, 
    LLMFileConfig, 
    DatasourceConfig, 
    DatasourceFileConfig,
    PolicyFileConfig
)

from nl2sql_cli.generators.env import EnvFileGenerator
from nl2sql_cli.generators.datasources import DatasourceGenerator
from nl2sql_cli.generators.llm import LLMGenerator
from nl2sql_cli.generators.policies import PolicyGenerator

from .factory import DemoDataFactory
from .writers.sqlite import SQLiteWriter
from .writers.docker import DockerWriter
from .defaults import (
    SAMPLE_QUESTIONS, 
    DEMO_POLICIES,
    DEMO_LLM_CONFIG,
    DEMO_LITE_DATASOURCES,
    DEMO_DOCKER_DATASOURCES
)

class DemoManager:
    """
    Manages the creation of the Demo Environment (Lite or Docker).
    Orchestrates Data Factory, Writers, and Generators.
    """

    def __init__(self, console: Console, project_root: pathlib.Path):
        self.console = console
        self.project_root = project_root
        self.config_manager = ConfigManager(project_root)
        self.factory = DemoDataFactory(seed=42)

    def print_step(self, msg: str):
        self.console.print(f"[dim]{msg}[/dim]")
    
    def print_success(self, msg: str):
        self.console.print(f"[green][OK][/green] {msg}")

    def print_error(self, msg: str):
        self.console.print(f"[red][ERROR] {msg}[/red]")

    def setup_lite(self, api_key: Optional[str] = None):
        """Sets up the SQLite-based demo environment."""
        data_dir = self.project_root / "data" / "demo_lite"
        self.print_step(f"Generating SQLite Databases in {data_dir}...")
        
        # 1. Generate Data
        ref = self.factory.get_ref_data()
        ops = self.factory.get_ops_data()
        supply = self.factory.get_supply_data()
        history = self.factory.get_history_data()
        
        # 2. Write DBs
        SQLiteWriter.write_lite(data_dir, ref, ops, supply, history)
        
        configs = DEMO_LITE_DATASOURCES
        
        # Write using Generator (Strict Typed)
        ds_configs = [DatasourceConfig(**c) for c in configs]
        file_config = DatasourceFileConfig(datasources=ds_configs)
        content = DatasourceGenerator.generate(file_config)
        ds_path = self.project_root / "configs" / "datasources.demo.yaml"
        ds_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ds_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        self._write_common_artifacts()
        
        self.print_step("Writing .env.demo configuration...")
        
        secrets = {}
        if api_key:
            secrets["OPENAI_API_KEY"] = api_key
            
        env_content = EnvFileGenerator.generate("demo", secrets=secrets)
        env_path = self.project_root / ".env.demo"
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(env_content)
            
        self.print_success("Lite Demo Setup Complete")


    def setup_docker(self, api_key: Optional[str] = None):
        """Sets up the Docker-based demo environment."""
        docker_dir = self.project_root / "demo_docker"
        self.print_step(f"Generating Docker Configuration in {docker_dir}...")
        
        # 1. Generate Data & Secrets
        secrets = self.factory.generate_secrets()
        if api_key:
            secrets["OPENAI_API_KEY"] = api_key
            
        ref = self.factory.get_ref_data()
        ops = self.factory.get_ops_data()
        supply = self.factory.get_supply_data()
        history = self.factory.get_history_data()
        
        # 2. Write Artifacts
        DockerWriter.write_docker(docker_dir, secrets, ref, ops, supply, history)
        
        self.print_step("Writing datasources config...")
        # Use defaults from configuration
        configs = DEMO_DOCKER_DATASOURCES
        

        ds_configs = [DatasourceConfig(**c) for c in configs]
        file_config = DatasourceFileConfig(datasources=ds_configs)
        content = DatasourceGenerator.generate(file_config)
        ds_path = self.project_root / "configs" / "datasources.demo.yaml"
        ds_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ds_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        self._write_common_artifacts()
        
        self.print_success("Docker Configuration Generated")
        return docker_dir

    def _write_common_artifacts(self):
        """Writes policies and sample questions."""
        self.print_step("Writing policies...")
        policy_config = PolicyFileConfig(roles=DEMO_POLICIES)
        content = PolicyGenerator.generate(policy_config)
        policy_path = self.project_root / "configs" / "policies.demo.json"
        
        if not policy_path.parent.exists():
             policy_path.parent.mkdir(parents=True, exist_ok=True)
             
        with open(policy_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        self.print_step("Writing sample questions...")
        import yaml
        samples_path = self.project_root / "configs" / "sample_questions.demo.yaml"
        with open(samples_path, "w") as f:
            yaml.dump(SAMPLE_QUESTIONS, f, sort_keys=False)
            
        self.print_step("Writing LLM config...")
        llm_config = LLMFileConfig(**DEMO_LLM_CONFIG)
        content = LLMGenerator.generate(llm_config)
        llm_path = self.project_root / "configs" / "llm.demo.yaml"
        with open(llm_path, "w", encoding="utf-8") as f:
            f.write(content)
            
    def start_docker_containers(self, docker_dir: pathlib.Path) -> bool:
        """Starts the docker containers using subprocess."""
        try:
            subprocess.run(
                ["docker", "compose", "-f", "docker-compose.demo.yml", "up", "-d"],
                cwd=docker_dir,
                check=True
            )
            return True
        except Exception as e:
            self.print_error(f"Failed to start Docker: {e}")
            return False

    def copy_docker_env_to_root(self, docker_dir: pathlib.Path) -> bool:
        """Copies the generated .env from docker dir to project root as .env.demo, using Protocol."""
        try:
            src = docker_dir / ".env"
            dest = self.project_root / ".env.demo"
            
            # Read secrets from source env
            secrets = {}
            if src.exists():
                with open(src, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            secrets[k.strip()] = v.strip()
            
            # Generate Standard Env Content with secrets injected
            content = EnvFileGenerator.generate("demo", secrets=secrets)
            
            # Write content
            with open(dest, "w", encoding="utf-8") as f:
                f.write(content)
                
            return True
        except Exception:
            return False

    def index_demo_data(self):
        """Triggers the indexing process for the demo."""
        
        from nl2sql.common.settings import settings, Settings
        from dotenv import load_dotenv
        
        env_path = self.project_root / ".env.demo"
        if env_path.exists():
            load_dotenv(env_path, override=True)
            new_settings = Settings()
            settings.__dict__.update(new_settings.__dict__)
        else:
            self.print_error(f"Could not find {env_path}")
            return False
        
        from nl2sql_cli.commands.indexing import run_indexing
        from nl2sql.services.vector_store import OrchestratorVectorStore
        from nl2sql.llm import LLMRegistry
        from nl2sql.configs import ConfigManager 
        from nl2sql.datasources import DatasourceRegistry 
        
        try:
            indexer_config_manager = ConfigManager(self.project_root)
            
            configs = indexer_config_manager.load_datasources()
            v_store = OrchestratorVectorStore(persist_directory=settings.vector_store_path)
            
            llm_registry = None
            try:
                llm_cfg = indexer_config_manager.load_llm()
                llm_registry = LLMRegistry(llm_cfg)
            except Exception:
                pass # Optional for schema indexing
                
            registry = DatasourceRegistry(configs)
            run_indexing(registry, settings.vector_store_path, v_store, llm_registry)
            return True
        except Exception as e:
            self.print_error(f"Indexing Failed: {e}")
            return False
