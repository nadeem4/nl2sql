
ENV_FILE_TEMPLATE = """# NL2SQL Configuration for '{env}'

# --- Configuration Paths ---
DATASOURCE_CONFIG=configs/datasources{suffix}.yaml
POLICIES_CONFIG=configs/policies{suffix}.json
SECRETS_CONFIG=configs/secrets{suffix}.yaml
LLM_CONFIG=configs/llm{suffix}.yaml
VECTOR_STORE=data/vector_store_{env}
SAMPLE_QUESTIONS=configs/sample_questions{suffix}.yaml
"""

# Settings appended for specific environments only.
ENV_SPECIFIC_SETTINGS = {
    "demo": (
        "\n# --- Embeddings ---\n"
        "# Local ONNX embeddings keep `nl2sql index` key-free. The first index run\n"
        "# downloads a ~79 MB model. Running a query still needs an LLM key.\n"
        "EMBEDDING_PROVIDER=local\n"
        "\n# --- Timeouts ---\n"
        "# A live run walks decomposer, planner, refiner, sql_agent and\n"
        "# synthesizer, each a real provider call; an observed OpenAI run took\n"
        "# 136s. The 60s default turned every `nl2sql demo` question in live\n"
        "# mode into a PIPELINE_TIMEOUT.\n"
        "GLOBAL_TIMEOUT_SEC=300\n"
        "\n# --- Run traces ---\n"
        "# The demo runs locally for you, so every run writes a trace to traces/:\n"
        "# each node's inputs and outputs, and each LLM prompt and raw response.\n"
        "# Inspect one with `nl2sql trace show <file>`; the library default is on_failure.\n"
        "TRACE_MODE=always\n"
    ),
}

ENV_SECRETS_HEADER = """
# --- Secrets ---
"""
