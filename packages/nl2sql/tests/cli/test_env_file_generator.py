from __future__ import annotations

from nl2sql.cli.generators.env.generator import EnvFileGenerator


def test_demo_env_selects_local_embeddings():
    content = EnvFileGenerator.generate("demo")

    assert "EMBEDDING_PROVIDER=local" in content
    assert "VECTOR_STORE=data/vector_store_demo" in content


def test_non_demo_env_keeps_default_embedding_provider():
    content = EnvFileGenerator.generate("dev")

    assert "EMBEDDING_PROVIDER" not in content


def test_secrets_are_appended_after_the_secrets_header():
    content = EnvFileGenerator.generate("demo", secrets={"OPENAI_API_KEY": "sk-test"})

    assert content.index("# --- Secrets ---") < content.index("OPENAI_API_KEY=sk-test")
    assert content.index("EMBEDDING_PROVIDER=local") < content.index("# --- Secrets ---")


def test_demo_env_gives_a_live_pipeline_room_to_finish():
    """60 seconds is not enough for a live multi-node LLM run.

    Every `nl2sql demo` question against a real provider died on
    `PIPELINE_TIMEOUT` (an observed run took 136s), so the demo environment
    raises the global timeout.
    """
    content = EnvFileGenerator.generate("demo")

    assert "GLOBAL_TIMEOUT_SEC=300" in content


def test_non_demo_env_keeps_the_default_timeout():
    assert "GLOBAL_TIMEOUT_SEC" not in EnvFileGenerator.generate("dev")
