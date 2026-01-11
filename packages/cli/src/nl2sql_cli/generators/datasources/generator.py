import yaml
from nl2sql.configs import DatasourceFileConfig

class DatasourceGenerator:
    """Generates the content for datasources.yaml."""

    HEADER = "# NL2SQL Datasource Configuration\n\n"

    @staticmethod
    def generate(config: DatasourceFileConfig) -> str:
        """
        Generates YAML content for Datasource configuration.
        
        Args:
            config: DatasourceFileConfig object (Envelope).
            
        Returns:
            Formatted YAML string.
        """
        dumped_config = config.model_dump(exclude_none=True)
        
        yaml_block = yaml.dump(dumped_config, sort_keys=False)
        
        return DatasourceGenerator.HEADER + yaml_block
