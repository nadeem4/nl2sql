import yaml
from nl2sql.configs import LLMFileConfig

class LLMGenerator:
    """Generates the content for llm.yaml."""

    HEADER = "# NL2SQL LLM Configuration\n\n"

    @staticmethod
    def generate(config: LLMFileConfig) -> str:
        """
        Generates YAML content for LLM configuration.
        
        Args:
            config: LLMFileConfig object (Envelope).
            
        Returns:
            Formatted YAML string.
        """
        dumped_config = config.model_dump(exclude_none=True)
        
        yaml_block = yaml.dump(dumped_config, sort_keys=False)
        
        return LLMGenerator.HEADER + yaml_block
