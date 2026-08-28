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
