"""The four store slots: ``StoreConfig``, the ``Storage`` sub-blocks, the ``store`` registry kind.

docs/architecture/phase2.md, section 4 ("The config", "The interfaces") and
the S1 row of section 12.
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from pydantic import BaseModel, ConfigDict, ValidationError

from nl2sql import ConfigError, Embeddings, Registry, Storage, StoreConfig
from nl2sql.runtime import Runtime
from nl2sql.testing.config import make_config


def _config(tmp_path: Path, storage: Storage):
    return make_config(tmp_path).model_copy(update={
        "storage": storage, "embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8))})


class MemoTracesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl_s: int = 60


class MemoTraces:
    """A plugin trace store, registered in code."""

    interface_version: ClassVar[int] = 1
    slot: ClassVar[str] = "traces"
    config_model: ClassVar[type[BaseModel]] = MemoTracesConfig

    def __init__(self, ttl_s: int):
        self.ttl_s = ttl_s

    @classmethod
    def from_config(cls, cfg: MemoTracesConfig, *, folder: Path) -> "MemoTraces":
        return cls(cfg.ttl_s)


def test_storage_with_only_path_is_valid(tmp_path):
    storage = Storage(path=tmp_path / ".nl2sql")

    assert storage.index == StoreConfig(type="chroma")
    assert storage.state == StoreConfig(type="sqlite")
    assert storage.traces == StoreConfig(type="files")
    assert storage.results == StoreConfig(type="local")

    runtime = Runtime.from_config(_config(tmp_path, storage))
    try:
        assert [type(s).slot for s in (runtime.index, runtime.state, runtime.traces, runtime.results)] \
            == ["index", "state", "traces", "results"]
        assert runtime.traces.directory == tmp_path / ".nl2sql" / "traces"
    finally:
        runtime.close()


def test_store_type_in_the_wrong_slot_names_the_installed_ones(tmp_path):
    storage = Storage(path=tmp_path / ".nl2sql", results=StoreConfig(type="chroma"))

    with pytest.raises(ConfigError) as error:
        Runtime.components(_config(tmp_path, storage))

    [problem] = error.value.problems
    assert problem.path == "storage.results"
    assert "'chroma' is an index store; results needs a results store. Installed: local." in problem.message


def test_unknown_store_type_suggests_the_closest_name(tmp_path):
    storage = Storage(path=tmp_path / ".nl2sql", index=StoreConfig(type="chrome"))

    with pytest.raises(ConfigError) as error:
        Runtime.components(_config(tmp_path, storage))

    assert "Did you mean 'chroma'?" in error.value.problems[0].message


def test_store_block_keys_are_validated_by_its_config_model(tmp_path):
    registry = Registry.default()
    registry.add_store("memo", MemoTraces)
    storage = Storage(path=tmp_path / ".nl2sql", traces=StoreConfig(type="memo", ttl=5))

    with pytest.raises(ConfigError) as error:
        Runtime.components(_config(tmp_path, storage), registry)

    [problem] = error.value.problems
    assert problem.path == "storage.traces"
    assert "ttl" in problem.message and "memo" in problem.message


def test_store_block_keeps_its_references_until_the_store_is_built(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMO_TTL", "30")
    config = make_config(tmp_path, storage={"path": ".nl2sql",
                                            "traces": {"type": "memo", "ttl_s": "${env:MEMO_TTL}"}})

    assert config.storage.path == tmp_path / ".nl2sql"
    assert config.storage.traces.model_extra == {"ttl_s": "${env:MEMO_TTL}"}

    registry = Registry.default()
    registry.add_store("memo", MemoTraces)
    from nl2sql.secrets import SecretManager
    from nl2sql.storage import build_store

    store = build_store("traces", config.storage.traces, folder=config.storage.path,
                        registry=registry, secrets=SecretManager(registry))
    assert isinstance(store, MemoTraces) and store.ttl_s == 30


def test_store_object_is_used_as_given(tmp_path):
    from nl2sql.secrets import SecretManager
    from nl2sql.storage import build_store

    traces = MemoTraces(ttl_s=1)
    storage = Storage(path=tmp_path, traces=traces)

    assert storage.traces is traces
    assert build_store("traces", storage.traces, folder=storage.path, registry=Registry.empty(),
                       secrets=SecretManager()) is traces


def test_store_object_in_the_wrong_slot_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="MemoTraces is a trace store; index needs an index store"):
        Storage(path=tmp_path, index=MemoTraces(ttl_s=1))


def test_a_plugin_state_store_is_used(tmp_path):
    class MemoState(MemoTraces):
        slot = "state"

    registry = Registry.default()
    registry.add_store("memo", MemoState)
    storage = Storage(path=tmp_path / ".nl2sql", state=StoreConfig(type="memo"))

    runtime = Runtime.components(_config(tmp_path, storage), registry)

    assert isinstance(runtime.state, MemoState)
    runtime.close()


def test_a_plugin_trace_store_is_used(tmp_path):
    registry = Registry.default()
    registry.add_store("memo", MemoTraces)
    storage = Storage(path=tmp_path / ".nl2sql", traces=StoreConfig(type="memo"))

    runtime = Runtime.components(_config(tmp_path, storage), registry)

    assert isinstance(runtime.traces, MemoTraces)


def test_store_registry_kind():
    registry = Registry.empty()

    @registry.store("memo")
    class Decorated(MemoTraces):
        pass

    assert registry.names("store") == ["memo"]
    assert registry.load("store", "memo") is Decorated
    assert {"chroma", "sqlite", "files", "local"} <= set(Registry.default().names("store"))


def test_store_interface_version_is_checked():
    registry = Registry.empty()

    class Old(MemoTraces):
        interface_version = 0

    registry.add_store("old", Old)
    with pytest.raises(ValueError, match="store interface 0; this engine needs 1"):
        registry.load("store", "old")
