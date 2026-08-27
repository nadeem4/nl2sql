
ENV_FILE_TEMPLATE = """# NL2SQL Configuration for '{env}'

# --- Configuration Paths ---
DATASOURCE_CONFIG=configs/datasources{suffix}.yaml
POLICIES_CONFIG=configs/policies{suffix}.json
SECRETS_CONFIG=configs/secrets{suffix}.yaml
LLM_CONFIG=configs/llm{suffix}.yaml
VECTOR_STORE=data/vector_store_{env}
ROUTING_EXAMPLES=configs/sample_questions{suffix}.yaml
"""

# Settings appended for specific environments only.
ENV_SPECIFIC_SETTINGS = {
    "demo": (
        "\n# --- Embeddings ---\n"
        "# Local ONNX embeddings keep `nl2sql index` key-free. The first index run\n"
        "# downloads a ~79 MB model. Running a query still needs an LLM key.\n"
        "EMBEDDING_PROVIDER=local\n"
    ),
}

ENV_SECRETS_HEADER = """
# --- Secrets ---
"""
