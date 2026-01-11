import json
from nl2sql.configs import PolicyFileConfig

class PolicyGenerator:
    """Generates the content for policies.json."""

    @staticmethod
    def generate(config: PolicyFileConfig) -> str:
        """
        Generates JSON content for Policy configuration.
        
        Args:
            config: PolicyFileConfig object (Envelope).
            
        Returns:
            Formatted JSON string.
        """
        json_dump = config.model_dump(mode="json")
        
        return json.dumps(json_dump, indent=2)
