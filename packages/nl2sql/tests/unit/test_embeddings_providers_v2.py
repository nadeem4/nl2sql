"""Embeddings providers, interface version 2: ``openai_compatible``, ``base_url``,
``dimension``, ``describe()`` and the one-call dimension probe.

See docs/architecture/phase2.md, section 5, "Embeddings".
"""
import tomllib
from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings as LangChainEmbeddings

from nl2sql.config import ConfigError, Embeddings
from nl2sql.embeddings import EmbeddingInfo
from nl2sql.embeddings.local import LOCAL
from nl2sql.embeddings.openai import OPENAI, OPENAI_COMPATIBLE
from nl2sql.registry import INTERFACE_VERSIONS, Registry
from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_config

FAKE_KEY = "-".join(["sk", "fake", "key", "for", "tests", "only"])
PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


class CountingEmbeddings(LangChainEmbeddings):
    """Returns vectors of ``size`` and counts every call."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return [[0.1] * self.size for _ in texts]

    def embed_query(self, text):
        self.calls += 1
        return [0.1] * self.size


class NoCalls(LangChainEmbeddings):
    def embed_documents(self, texts):
        raise AssertionError("the model was called")

    def embed_query(self, text):
        raise AssertionError("the model was called")


def test_embeddings_interface_is_version_2():
    assert INTERFACE_VERSIONS["embeddings"] == 2
    for provider in (LOCAL, OPENAI, OPENAI_COMPATIBLE):
        assert provider.interface_version == 2


# ------------------------------------------------------------ openai_compatible

def test_openai_compatible_needs_a_model_and_base_url():
    with pytest.raises(ValueError, match="model"):
        OPENAI_COMPATIBLE.create(Embeddings(provider="openai_compatible", base_url="http://ollama:11434/v1"))
    with pytest.raises(ValueError, match="base_url"):
        OPENAI_COMPATIBLE.create(Embeddings(provider="openai_compatible", model="nomic-embed-text"))


def test_openai_compatible_passes_base_url_and_sends_plain_text():
    embedder = OPENAI_COMPATIBLE.create(Embeddings(
        provider="openai_compatible", base_url="http://ollama:11434/v1", model="nomic-embed-text"))

    assert embedder.openai_api_base == "http://ollama:11434/v1"
    assert embedder.model == "nomic-embed-text"
    # Other servers take text, not OpenAI's token ids.
    assert embedder.check_embedding_ctx_length is False
    # No key needed: a placeholder, since the OpenAI client refuses to start without one.
    assert embedder.openai_api_key.get_secret_value()


def test_openai_compatible_uses_the_configured_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    embedder = OPENAI_COMPATIBLE.create(Embeddings(
        provider="openai_compatible", base_url="https://x.openai.azure.com/openai/v1/",
        model="embed-prod", api_key=FAKE_KEY))

    assert embedder.openai_api_key.get_secret_value() == FAKE_KEY


def test_openai_compatible_describes_its_dimension_only_when_written():
    cfg = Embeddings(provider="openai_compatible", base_url="http://h/v1", model="nomic-embed-text")
    assert OPENAI_COMPATIBLE.describe(cfg) == EmbeddingInfo("openai_compatible", "nomic-embed-text", None)
    assert OPENAI_COMPATIBLE.describe(cfg.model_copy(update={"dimension": 768})).dimension == 768


# ------------------------------------------------------------ openai and local

def test_openai_passes_base_url():
    embedder = OPENAI.create(Embeddings(provider="openai", api_key=FAKE_KEY, base_url="http://gateway/v1"))
    assert embedder.openai_api_base == "http://gateway/v1"


def test_openai_knows_its_own_models_dimension():
    assert OPENAI.describe(Embeddings(provider="openai")) == EmbeddingInfo(
        "openai", "text-embedding-3-small", 1536)
    assert OPENAI.describe(Embeddings(provider="openai", model="text-embedding-3-large")).dimension == 3072
    assert OPENAI.describe(Embeddings(provider="openai", model="someone-elses")).dimension is None
    assert OPENAI.describe(Embeddings(provider="openai", model="someone-elses", dimension=512)).dimension == 512


def test_base_url_is_an_error_for_local():
    with pytest.raises(ValueError, match="base_url"):
        LOCAL.create(Embeddings(provider="local", base_url="http://h/v1"))


def test_local_describes_its_one_model():
    assert LOCAL.describe(Embeddings(provider="local")) == EmbeddingInfo("local", "all-MiniLM-L6-v2", 384)


def test_local_rejects_another_dimension():
    with pytest.raises(ValueError, match="384"):
        LOCAL.create(Embeddings(provider="local", dimension=768))


def test_dimension_must_be_positive():
    with pytest.raises(ValueError):
        Embeddings(provider="local", dimension=0)


def test_a_bad_section_is_a_config_error(tmp_path):
    cfg = make_config(tmp_path, embeddings={"provider": "local", "base_url": "http://h/v1"})
    with pytest.raises(ConfigError, match="base_url"):
        Runtime.components(cfg)


def test_no_azure_embeddings_provider_is_built_in():
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["entry-points"]["nl2sql.embeddings"]

    assert set(declared) == {"local", "openai", "openai_compatible"}
    assert not any("azure" in name for name in Registry.default().names("embeddings"))


# ------------------------------------------------------------ the probe

class _Provider:
    """A registered provider whose embedder is the test's, and whose dimension is ``dimension``."""

    interface_version = 2
    default_model = "m"
    requires = ()
    install_hint = ""

    def __init__(self, embedder, dimension):
        self.embedder, self.dimension = embedder, dimension

    def create(self, cfg):
        return self.embedder

    def describe(self, cfg):
        return EmbeddingInfo("mine", cfg.model or self.default_model, cfg.dimension or self.dimension)


def _runtime(tmp_path, provider, **section):
    registry = Registry.default()
    registry.add_embeddings("mine", provider)
    return Runtime.components(make_config(tmp_path, embeddings={"provider": "mine", **section}), registry)


def test_known_dimension_needs_no_call(tmp_path):
    runtime = _runtime(tmp_path, _Provider(NoCalls(), dimension=None), dimension=768)

    assert runtime.embedding_info == EmbeddingInfo("mine", "m", 768)


def test_a_provider_that_knows_its_dimension_needs_no_call(tmp_path):
    runtime = _runtime(tmp_path, _Provider(NoCalls(), dimension=384))

    assert runtime.embedding_info.dimension == 384


def test_building_the_engine_makes_no_call(tmp_path):
    embedder = CountingEmbeddings(size=5)
    _runtime(tmp_path, _Provider(embedder, dimension=None))

    assert embedder.calls == 0


def test_unknown_dimension_is_probed_once(tmp_path):
    embedder = CountingEmbeddings(size=5)
    runtime = _runtime(tmp_path, _Provider(embedder, dimension=None))

    assert runtime.embedding_info == EmbeddingInfo("mine", "m", 5)
    assert runtime.embedding_info.dimension == 5
    assert runtime.derive({}).embedding_info.dimension == 5
    assert embedder.calls == 1


def test_an_instance_is_probed(tmp_path):
    embedder = CountingEmbeddings(size=7)
    cfg = make_config(tmp_path).model_copy(update={"embeddings": Embeddings(instance=embedder)})
    runtime = Runtime.components(cfg)

    info = runtime.embedding_info
    assert info.dimension == 7
    assert info.model == "CountingEmbeddings"
    assert embedder.calls == 1
