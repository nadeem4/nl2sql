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

from nl2sql.datasets.demo import (
    DEMO_CURATED,
    DEMO_DATABASES,
    DEMO_DATASOURCES,
    DEMO_POLICIES,
    DEMO_QUESTIONS_BY_DATASOURCE,
)
from .defaults import DEMO_LLM_CONFIG
from .stamp import write_stamp


class _CuratedDumper(yaml.SafeDumper):
    """Writes multi-line strings (the SQL) as ``|`` blocks, so the file reads as written."""


_CuratedDumper.add_representer(
    str,
    lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None),
)

CURATED_HEADER = """# The demo's verified queries: SQL a person wrote and reviewed, with typed
# params, and the questions each one answers. Written by `nl2sql demo`.
#
# Check it with `nl2sql --env demo curated validate`, run every query once with
# `nl2sql --env demo curated test`, and after an edit re-index its questions
# with `nl2sql --env demo index --curated-only`.
# See docs/configuration/curated.md.
"""


def _v1_datasource(config: Dict[str, Any]) -> Dict[str, Any]:
    """A demo datasource (``nl2sql.yaml`` shape) in the 0.4 file shape ``setup --demo`` writes.

    ``setup`` is deleted in 0.5 together with this function; ``nl2sql init
    --demo`` writes the new config.
    """
    connection = dict(config["connection"])
    connection["database"] = f"data/{connection['database']}"
    return {
        "id": config["id"],
        "description": config["description"],
        "connection": {"type": config["type"], **connection},
        "options": dict(config["limits"]),
    }


def _v1_policies(roles: Dict[str, Any]) -> Dict[str, Any]:
    """The demo roles with the ``role`` field the 0.4 policy file repeats."""
    return {name: {**role, "role": name} for name, role in roles.items()}


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
    """Creates the demo project: the three demo databases, their configs and `.env.demo`."""

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

    def setup_demo(self, api_key: Optional[str] = None):
        """Sets up the demo environment: three linked sample databases.

        ``chinook`` (a digital music store), ``support`` (its help desk) and
        ``webanalytics`` (its website traffic). The latter two are synthetic and
        share Chinook's customer names and email addresses.
        """
        data_dir = self.project_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        for _, file_name, _ in DEMO_DATABASES:
            db_path = data_dir / file_name
            self.print_step(f"Copying {file_name} to {db_path}...")
            packaged = importlib.resources.files("nl2sql.datasets") / file_name
            with importlib.resources.as_file(packaged) as source:
                shutil.copyfile(source, db_path)

        self.print_step("Writing datasources config...")
        ds_configs = [DatasourceConfig(**_v1_datasource(config)) for config in DEMO_DATASOURCES]
        file_config = DatasourceFileConfig(datasources=ds_configs)
        content = DatasourceGenerator.generate(file_config)
        ds_path = self.project_root / "configs" / "datasources.demo.yaml"
        ds_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ds_path, "w", encoding="utf-8") as f:
            f.write(content)

        self._write_common_artifacts(_v1_policies(DEMO_POLICIES), DEMO_QUESTIONS_BY_DATASOURCE)

        self.print_step("Writing .env.demo configuration...")
        secrets = {}
        if api_key:
            secrets[env_var_for_key(api_key)] = api_key

        env_content = EnvFileGenerator.generate("demo", secrets=secrets)
        env_path = self.project_root / ".env.demo"
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(env_content)

        write_stamp(self.project_root)
        self.print_success(
            "Demo Setup Complete: " + ", ".join(ds_id for ds_id, _, _ in DEMO_DATABASES)
        )

    def _write_common_artifacts(self, policies: Dict[str, Any], questions: Dict[str, List[str]]):
        """Writes policies, sample questions, verified queries and the LLM config."""
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

        self.print_step("Writing verified queries...")
        curated_path = self.project_root / "configs" / "curated.demo.yaml"
        with open(curated_path, "w", encoding="utf-8") as f:
            f.write(CURATED_HEADER)
            yaml.dump({"version": 1, **DEMO_CURATED}, f, Dumper=_CuratedDumper, sort_keys=False, allow_unicode=True, width=120)

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
        # Runtime validates vector store settings during construction.
        # Its settings override the shell's, except the provider keys: a key
        # already exported wins over `.env.demo` (the precedence `nl2sql demo`
        # documents), and the file's empty `OPENAI_API_KEY=` placeholder must
        # never blank it, or enrichment runs with no key at all.
        _load_demo_env(env_path)
        reload_settings()

        from nl2sql._legacy import legacy_runtime
        from nl2sql.cli.common.indexing import run_indexing

        try:
            ctx = legacy_runtime(
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
