# Default Configurations for Demo

from nl2sql.llm.providers import DEFAULT_OPENAI_MODEL

DEMO_LLM_CONFIG = {
    "default": {
        "provider": "openai",
        "model": DEFAULT_OPENAI_MODEL,
        "api_key": "${env:OPENAI_API_KEY}"
    }
}
