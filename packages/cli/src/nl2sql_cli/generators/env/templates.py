
ENV_FILE_TEMPLATE = """# NL2SQL Configuration for '{env}'

# --- Configuration Paths ---
DATASOURCE_CONFIG=configs/datasources{suffix}.yaml
POLICIES_CONFIG=configs/policies{suffix}.json
SECRETS_CONFIG=configs/secrets{suffix}.yaml
LLM_CONFIG=configs/llm{suffix}.yaml
BENCHMARK_CONFIG=configs/benchmark_suite.yaml
VECTOR_STORE=data/vector_store_{env}
ROUTING_EXAMPLES=configs/sample_questions{suffix}.yaml

# --- Secrets ---
"""
