from typing import Dict, Optional
from .templates import ENV_FILE_TEMPLATE

class EnvFileGenerator:
    """Standard generator for .env files complying with Universal Environment Protocol."""
    
    @staticmethod
    def generate(env: str, secrets: Optional[Dict[str, str]] = None) -> str:
        """Generates the content for a .env.<env> file.

        This method constructs the standard configuration paths based on the
        environment name and optionally appends provided secrets.

        Args:
            env: The environment name (e.g., 'dev', 'demo', 'prod').
            secrets: Optional dictionary of secret key-values to append.

        Returns:
            The formatted string content for the .env file.
        """
        suffix = f".{env}" if env != "dev" else ""
        
        # Populate template
        content = ENV_FILE_TEMPLATE.format(env=env, suffix=suffix)
        
        used_keys = set()
        
        if secrets:
            for key, value in secrets.items():
                content += f"{key}={value}\n"
                used_keys.add(key)
        
        critical_placeholders = ["OPENAI_API_KEY"]
        appended_placeholders = False
        
        for ph in critical_placeholders:
            if ph not in used_keys:
                content += f"{ph}=\n"
                appended_placeholders = True
        
        if appended_placeholders and not secrets:
            content += "# Please fill in the required secrets above.\n"
            
        return content
