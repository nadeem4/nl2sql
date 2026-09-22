from typing import Dict, Any, List, Optional
import importlib.resources
import pathlib
import shutil
import yaml
from rich.console import Console
from rich.markup import escape
from nl2sql.configs import (
    ConfigManager,
    LLMFileConfig,
    DatasourceConfig,
    DatasourceFileConfig,
    PolicyFileConfig
)
from nl2sql.configs.secrets import SecretsFileConfig

from nl2sql.llm.providers import PROVIDER_KEYS, env_var_for_key
from nl2sql.cli.generators.env import EnvFileGenerator
from nl2sql.cli.generators.datasources import DatasourceGenerator
from nl2sql.cli.generators.llm import LLMGenerator
from nl2sql.cli.generators.policies import PolicyGenerator

from .chinook import CHINOOK_DATASOURCE, CHINOOK_POLICIES, CHINOOK_QUESTIONS
from .defaults import DEMO_LLM_CONFIG
from .stamp import write_stamp


def _load_demo_env(env_path: pathlib.Path) -> None:
    """Loads `.env.demo` over the environment, keeping any provider key already set."""
    import os

    from dotenv import dotenv_values

    for name, value in dotenv_values(env_path).items():
        if value is None:
            continue
        if name in PROVIDER_KEYS and os.environ.get(name):
            continue
        os.environ[name] = value


class DemoManager:
    """Creates the demo project: the Chinook database, its configs and `.env.demo`."""

    def __init__(self, console: Console, project_root: pathlib.Path):
        self.console = console
        self.project_root = project_root
        self.config_manager = ConfigManager(project_root)

    def print_step(self, msg: str):
        self.console.print(f"[dim]{escape(str(msg))}[/dim]")

    def print_success(self, msg: str):
        self.console.print(f"[green][OK][/green] {escape(str(msg))}")

    def print_error(self, msg: str):
        self.console.print(f"[red][ERROR] {escape(str(msg))}[/red]")

    def setup_chinook(self, api_key: Optional[str] = None):
        """Sets up the Chinook (digital music store) demo environment."""
        db_path = self.project_root / "data" / "chinook.sqlite"
        self.print_step(f"Copying the Chinook database to {db_path}...")

        db_path.parent.mkdir(parents=True, exist_ok=True)
        packaged = importlib.resources.files("nl2sql.cli.demo") / "data" / "chinook.sqlite"
        with importlib.resources.as_file(packaged) as source:
            shutil.copyfile(source, db_path)

        self.print_step("Writing datasources config...")
        ds_configs = [DatasourceConfig(**CHINOOK_DATASOURCE)]
        file_config = DatasourceFileConfig(datasources=ds_configs)
        content = DatasourceGenerator.generate(file_config)
        ds_path = self.project_root / "configs" / "datasources.demo.yaml"
        ds_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ds_path, "w", encoding="utf-8") as f:
            f.write(content)

        self._write_common_artifacts(CHINOOK_POLICIES, {"chinook": CHINOOK_QUESTIONS})

        self.print_step("Writing .env.demo configuration...")
        secrets = {}
        if api_key:
            secrets[env_var_for_key(api_key)] = api_key

        env_content = EnvFileGenerator.generate("demo", secrets=secrets)
        env_path = self.project_root / ".env.demo"
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(env_content)

        write_stamp(self.project_root)
        self.print_success("Chinook Demo Setup Complete")

    def _write_common_artifacts(self, policies: Dict[str, Any], questions: Dict[str, List[str]]):
        """Writes policies, sample questions and the LLM config."""
        self.print_step("Writing policies...")
        policy_config = PolicyFileConfig(roles=policies)
        content = PolicyGenerator.generate(policy_config)
        policy_path = self.project_root / "configs" / "policies.demo.json"

        if not policy_path.parent.exists():
             policy_path.parent.mkdir(parents=True, exist_ok=True)

        with open(policy_path, "w", encoding="utf-8") as f:
            f.write(content)

        self.print_step("Writing sample questions...")
        samples_path = self.project_root / "configs" / "sample_questions.demo.yaml"
        with open(samples_path, "w") as f:
            yaml.dump(questions, f, sort_keys=False)

        self.print_step("Writing LLM config...")
        llm_config = LLMFileConfig(**DEMO_LLM_CONFIG)
        content = LLMGenerator.generate(llm_config)
        llm_path = self.project_root / "configs" / "llm.demo.yaml"
        with open(llm_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Every generated `.env.demo` sets SECRETS_CONFIG, so the envelope has
        # to exist even though the demo configures no secret provider.
        self.print_step("Writing secrets envelope...")
        secrets_path = self.project_root / "configs" / "secrets.demo.yaml"
        with open(secrets_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(SecretsFileConfig().model_dump(), f, sort_keys=False)

    def index_health(self):
        """Health of the demo's vector index, read from its contents.

        Reads the paths from `.env.demo` without loading it, and loads no
        embedding model, so it is cheap enough to run on every start.
        """
        from dotenv import dotenv_values
        from nl2sql.common.settings import settings
        from nl2sql.indexing.health import inspect_index_at

        env_path = self.project_root / ".env.demo"
        values = dotenv_values(env_path) if env_path.exists() else {}
        ds_path = self.project_root / (values.get("DATASOURCE_CONFIG") or "configs/datasources.demo.yaml")
        try:
            raw = yaml.safe_load(ds_path.read_text(encoding="utf-8")) or {}
            datasource_ids = [d["id"] for d in raw.get("datasources") or [] if d.get("id")]
        except (OSError, yaml.YAMLError, KeyError, TypeError):
            datasource_ids = []
        return inspect_index_at(
            self.project_root / (values.get("VECTOR_STORE") or "data/vector_store_demo"),
            values.get("VECTOR_STORE_COLLECTION") or settings.vector_store_collection_name,
            self.project_root / (values.get("SCHEMA_STORE_PATH") or "data/schema_store.db"),
            datasource_ids,
        )

    def index_demo_data(self, enrich: bool = False) -> bool:
        """Rebuilds the demo's index. Returns whether the new index is live.

        Enrichment is off unless asked for: it spends tokens on the user's key.
        """

        from nl2sql.common.settings import settings, reload_settings

        env_path = self.project_root / ".env.demo"
        if not env_path.exists():
            self.print_error(f"Could not find {env_path}")
            return False

        # The demo environment must be active before the context is built:
        # NL2SQLContext validates vector store settings during construction.
        # Its settings override the shell's, except the provider keys: a key
        # already exported wins over `.env.demo` (the precedence `nl2sql demo`
        # documents), and the file's empty `OPENAI_API_KEY=` placeholder must
        # never blank it, or enrichment runs with no key at all.
        _load_demo_env(env_path)
        reload_settings()

        from nl2sql.context import NL2SQLContext
        from nl2sql.cli.commands.indexing import run_indexing

        try:
            ctx = NL2SQLContext(
                ds_config_path=self.project_root / settings.datasource_config_path,
                secrets_config_path=self.project_root / settings.secrets_config_path,
                llm_config_path=self.project_root / settings.llm_config_path,
                vector_store_path=self.project_root / settings.vector_store_path,
                policies_config_path=self.project_root / settings.policies_config_path,
            )
            run_indexing(ctx, enrich=enrich)
            return True
        except SystemExit:
            # run_indexing exits 1 on any failure, after printing why. The demo
            # keeps going: the playground shows the problem and offers Rebuild.
            return False
        except Exception as e:
            self.print_error(f"Indexing Failed: {e}")
            return False
