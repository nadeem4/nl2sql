import yaml
from nl2sql.configs import LLMFileConfig
from nl2sql.configs.llm import REVEAL_SECRETS

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
        # The file must hold the real key or ``${env:...}`` reference, not the mask.
        dumped_config = config.model_dump(mode="json", exclude_none=True,
                                          context={REVEAL_SECRETS: True})

        # ``temperature: null`` means "send no temperature". Dropped with the
        # other None fields, it would reload as the 0.0 default.
        if config.default.temperature is None:
            dumped_config["default"]["temperature"] = None
        for key, agent in (config.agents or {}).items():
            if agent.temperature is None:
                dumped_config["agents"][key]["temperature"] = None

        yaml_block = yaml.safe_dump(dumped_config, sort_keys=False)
        
        return LLMGenerator.HEADER + yaml_block
