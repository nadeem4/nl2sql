"""
Example usage of the NL2SQL Public API
"""

from nl2sql import NL2SQL

def example_usage():
    """
    Example demonstrating the usage of the NL2SQL Public API
    """
    print("NL2SQL Public API Example")
    print("=" * 30)
    
    # Initialize the engine
    # Note: This will fail without proper config files, but shows the API usage
    try:
        engine = NL2SQL()
        print("+ Engine initialized")
    except Exception as e:
        print(f"! Engine initialization failed (expected without config): {type(e).__name__}")
        # Create a minimal example without config for demonstration
        engine = None
    
    print("\n1. Adding a datasource programmatically:")
    print("""   
    engine.add_datasource({
        "id": "example_db",
        "description": "Example database",
        "connection": {
            "type": "sqlite",
            "database": "example.db"
        }
    })""")
    
    print("\n2. Running a natural language query:")
    print("""
    result = engine.run_query(
        "Show top 10 customers by revenue",
        datasource_id="example_db"
    )
    print(result.final_answer)""")
    
    print("\n3. Indexing a datasource:")
    print("""
    stats = engine.index_datasource("example_db")
    print(f"Indexed {stats['chunks']} chunks")""")
    
    print("\n4. Listing all datasources:")
    print("""
    datasources = engine.list_datasources()
    print(datasources)""")
    
    print("\n5. Configuring LLM programmatically:")
    print("""
    engine.configure_llm({
        "name": "gpt-4o-mini",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "${OPENAI_API_KEY}",
        "temperature": 0.0
    })""")

if __name__ == "__main__":
    example_usage()