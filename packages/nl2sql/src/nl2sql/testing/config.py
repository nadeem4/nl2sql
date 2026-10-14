"""``make_config``: a project folder with a valid ``nl2sql.yaml``, for tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from nl2sql.config import Config, load_config

DEFAULT_SECTIONS: dict = {
    "datasources": [],
    "llm": {"provider": "openai", "model": "gpt-test"},
    "embeddings": {"provider": "local"},
    "policies": {"allow_all": True},
    "storage": {"path": ".nl2sql"},
}


def make_config(folder: Path, **sections: Any) -> Config:
    """Write ``folder/nl2sql.yaml`` and return the ``Config`` it loads to.

    Each keyword replaces one section, written as it would be in YAML (plain
    dicts and lists). Without any, the config has no datasources, an OpenAI
    LLM with no key, local embeddings, ``allow_all`` policies and
    ``storage.path`` at ``folder/.nl2sql``. Point ``llm.base_url`` at
    ``nl2sql.testing.fake_llm`` to run queries without a real model.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    document = {"version": 2, **DEFAULT_SECTIONS, **sections}
    path = folder / "nl2sql.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return load_config(path)
