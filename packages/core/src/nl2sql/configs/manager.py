
import yaml
import json
import shutil
import pathlib
from typing import List, Dict, Any, Optional, Union
from pydantic import ValidationError

from nl2sql.common.settings import settings
from .datasources import DatasourceConfig, DatasourceFileConfig
from .llm import LLMFileConfig
from .policies import PolicyFileConfig
from .secrets import SecretProviderConfig, SecretsFileConfig
from .sample_questions import SampleQuestionsFileConfig

class ConfigManager:
    """
    Centralized manager for reading and writing application configuration.
    Enforces consistency and handles file I/O for Datasources, LLMs, Policies, and Secrets.
    """

    def __init__(self, project_root: Optional[pathlib.Path] = None):
        """
        Args:
            project_root: Optional override for project root. 
                          If None, uses settings paths or CWD resolution strategy.
        """
        self.project_root = project_root
        
        # Resolve root: Use override, or CWD
        root = self.project_root or pathlib.Path.cwd()
        
        # Default paths from settings if not overridden
        self._ds_path = root / settings.datasource_config_path
        self._llm_path = root / settings.llm_config_path
        self._policy_path = root / settings.policies_config_path
        self._secrets_path = root / settings.secrets_config_path
        self._sample_questions_path = root / settings.sample_questions_path

    def ensure_config_dirs(self) -> None:
        """Ensures that the configuration directories exist."""
        for path in [self._ds_path, self._llm_path, self._policy_path, self._secrets_path]:
            if not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)

    def _resolve_secrets(self, obj: Any) -> Any:
        """Helper to resolve secrets using the Secrets Manager."""
        try:
            from nl2sql.secrets.manager import secret_manager
            return secret_manager.resolve_object(obj)
        except ImportError:
            return obj


    def load_datasources(self, path: Optional[pathlib.Path] = None) -> List[DatasourceConfig]:
        """
        Loads datasource configurations from YAML.
        Handles Legacy (Dict) vs V2 (List under 'datasources' key) formats.
        """
        target_path = path or self._ds_path
        
        if not target_path.exists():
            raise FileNotFoundError(f"Datasource config not found: {target_path}")

        try:
            content = target_path.read_text(encoding="utf-8")
            raw = yaml.safe_load(content) or {}
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to load datasource configs") from exc
        except Exception as e:
            raise ValueError(f"Failed to parse YAML from {target_path}: {e}")

        try:
            # Structurally validate the file envelope
            file_config = DatasourceFileConfig.model_validate(raw)
            configs = file_config.datasources
            return self._resolve_secrets(configs)
        except ValidationError as e:
            raise ValueError(f"Datasource Configuration Invalid: {e}")

    def load_llm(self, path: Optional[pathlib.Path] = None) -> LLMFileConfig:
        """
        Loads LLM configuration. 
        Returns nl2sql.configs.LLMFileConfig object.
        """
        target_path = path or self._llm_path
        
        if not target_path.exists():
             raise FileNotFoundError(f"LLM config not found: {target_path}")

        try:
            data = yaml.safe_load(target_path.read_text()) or {}
            
            # Use File Config for validation
            config = LLMFileConfig.model_validate(data)
            return self._resolve_secrets(config)
        except ValidationError as e:
            raise ValueError(f"LLM Configuration Invalid: {e}")



    def load_policies(self, path: Optional[pathlib.Path] = None) -> PolicyFileConfig:
        """
        Loads Policy configuration.
        Returns nl2sql.configs.PolicyFileConfig object.
        """
        target_path = path or self._policy_path
        
        if not target_path.exists():
            raise FileNotFoundError(f"Policy config not found: {target_path}")
            
        try:
            with open(target_path, "r") as f:
                raw_json = f.read()
                
            data = json.loads(raw_json)
            # Use File Config for validation
            return PolicyFileConfig.model_validate(data)
        except ValidationError as ve:
            raise ValueError(f"Policy Schema Validation Failed: {ve}")
        except Exception as e:
            raise ValueError(f"Failed to load policies: {e}")



    def load_secrets(self, path: Optional[pathlib.Path] = None) -> List[SecretProviderConfig]:
        """Loads Secret configurations."""
        target_path = path or self._secrets_path
        if not target_path.exists():
            return []
            
        try:
            content = target_path.read_text(encoding="utf-8")
            raw = yaml.safe_load(content) or []
            
            file_config = SecretsFileConfig.model_validate(raw)
            configs = file_config.providers
            
            return self._resolve_secrets(configs)
        except ValidationError as e:
            raise ValueError(f"Secret Configuration Invalid: {e}")
        except Exception as e:
            raise ValueError(f"Failed to load secrets: {e}")


    def load_sample_questions(self, path: Optional[pathlib.Path] = None, ds_id: Optional[str] = None) -> Union[Dict[str, List[str]], List[str]]:
        """Loads sample questions from a YAML file."""
        target_path = path or self._sample_questions_path
        
        if not target_path.exists():
            raise FileNotFoundError(f"Sample questions file not found: {target_path}")
            
        try:
            raw = yaml.safe_load(target_path.read_text(encoding="utf-8")) or {}
            
            if ds_id:
                return raw.get(ds_id, [])

            return raw
        except Exception as e:
            raise ValueError(f"Failed to load sample questions: {e}")

    def get_example_questions(self, datasource_id: str) -> List[str]:
        """Returns example questions for a datasource, if configured."""
        try:
            questions = self.load_sample_questions(ds_id=datasource_id)
            return questions or []
        except FileNotFoundError:
            return []




