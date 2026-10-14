"""A new config folder (``init_project``) and the demo's config (``demo_config``).

``nl2sql init`` calls ``init_project``; ``NL2SQL.demo()`` builds on
``demo_config``. ``init_project(demo=True)`` writes ``demo_config`` out as
files, so the two cannot drift apart. See
docs/architecture/configuration_v2.md, section 10.

No key is ever written: ``llm.yaml`` names none, and the provider's standard
variable is the fallback.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from nl2sql.config.loader import load_config
from nl2sql.config.models import (
    LLM,
    Config,
    Curated,
    Datasource,
    Embeddings,
    PipelineSettings,
    Policies,
    Role,
    Settings,
    Storage,
    TraceSettings,
)

PathLike = Union[str, "os.PathLike[str]"]

DEMO_STORAGE_PATH = Path(".nl2sql-demo")
STORAGE_PATH = ".nl2sql"
DATA_FOLDER = "data"
GITIGNORE_LINES = (f"{STORAGE_PATH}/", ".env")


def default_llm(provider: str) -> LLM:
    """The ``LLM`` a new config starts with for ``provider``: its default model, and no key."""
    from nl2sql.llm.providers import DEFAULT_MODELS, default_temperature_for

    if provider not in DEFAULT_MODELS:
        raise ValueError(f"'{provider}' has no default model to start a config with. "
                         f"Choose one of: {', '.join(sorted(DEFAULT_MODELS))}.")
    return LLM(provider=provider, model=DEFAULT_MODELS[provider],
               temperature=default_temperature_for(provider))


def demo_config(
    *,
    llm: Optional[LLM] = None,
    path: PathLike = DEMO_STORAGE_PATH,
    data_dir: Optional[PathLike] = None,
) -> Config:
    """The demo: three linked SQLite databases, their roles and verified queries.

    The databases are opened read-only from ``data_dir``, by default the
    folder the package installed them in. Each datasource carries its sample
    questions. Without ``llm``, the default OpenAI model is used.

    Its settings are the demo's own: a live run walks six model calls (one
    OpenAI run took 136 s), so a question may take 300 s; every run writes a
    trace, which the playground's Debug drill-down reads; and a refusal names
    the table, since showing one is the demo's point.
    """
    from nl2sql.datasets import CHINOOK_DB_PATH
    from nl2sql.datasets.demo import DEMO_CURATED, DEMO_DATASOURCES, DEMO_POLICIES, DEMO_QUESTIONS_BY_DATASOURCE

    folder = Path(data_dir) if data_dir is not None else CHINOOK_DB_PATH.parent
    datasources = [
        Datasource(**{
            **source,
            "connection": {**source["connection"], "database": (folder / source["connection"]["database"]).as_posix()},
            "sample_questions": DEMO_QUESTIONS_BY_DATASOURCE[source["id"]],
        })
        for source in DEMO_DATASOURCES
    ]
    return Config(
        datasources=datasources,
        llm=llm if llm is not None else default_llm("openai"),
        embeddings=Embeddings(provider="local"),
        policies=Policies(roles={name: Role(**role) for name, role in DEMO_POLICIES.items()},
                          refusal_names_tables=True),
        storage=Storage(path=Path(path)),
        curated=Curated(**DEMO_CURATED),
        settings=Settings(pipeline=PipelineSettings(timeout_s=300), traces=TraceSettings(mode="always")),
    )


def init_project(
    folder: PathLike = ".",
    *,
    demo: bool = False,
    provider: str = "openai",
    force: bool = False,
) -> List[Path]:
    """Write a new config into ``folder``, check it with ``load_config``, and return the files written.

    ``nl2sql.yaml`` is the table of contents, and each section is in its own
    file. With ``demo``, the config is ``demo_config`` and the three databases
    are copied into ``data/``. A file that already exists is an error naming
    it, and nothing is written, unless ``force`` is set. ``.gitignore`` is
    never overwritten: the lines it is missing are added.
    """
    folder = Path(folder)
    llm = default_llm(provider)
    files: Dict[str, str] = {}
    copies: Dict[str, Path] = {}

    sections = ["datasources", "llm", "embeddings", "policies", "storage", *(["curated", "settings"] if demo else [])]
    files["nl2sql.yaml"] = TOC_HEADER + "version: 2\n" + "".join(
        f"{section + ':':<13}{{ file: {_section_file(section)} }}\n" for section in sections)
    files["llm.yaml"] = _llm_file(llm)
    files["embeddings.yaml"] = EMBEDDINGS_HEADER + _dump({"provider": "local"})
    files["storage.yaml"] = STORAGE_HEADER + _dump({"path": STORAGE_PATH})
    if demo:
        config = demo_config(llm=llm, path=STORAGE_PATH, data_dir=DATA_FOLDER)
        for datasource in config.datasources:
            files[f"datasources/{datasource.id}.yaml"] = _datasource_file(datasource)
        for source in _packaged_databases():
            copies[f"{DATA_FOLDER}/{source.name}"] = source
        files["policies.yaml"] = _dump(config.policies.model_dump(mode="json", exclude_defaults=True))
        files["curated.yaml"] = CURATED_HEADER + _dump(config.curated.model_dump(mode="json", exclude_defaults=True))
        files["settings.yaml"] = DEMO_SETTINGS_HEADER + _dump(
            config.settings.model_dump(mode="json", by_alias=True, exclude_defaults=True))
    else:
        files["datasources/example.yaml"] = EXAMPLE_DATASOURCE
        files["policies.yaml"] = POLICIES_HEADER + _dump({"allow_all": True})

    if not force:
        existing = sorted(name for name in [*files, *copies] if (folder / name).exists())
        if existing:
            raise FileExistsError(
                f"{folder.as_posix()} already has {', '.join(existing)}. Overwriting them needs force."
            )

    written: List[Path] = []
    for name, text in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(target)
    for name, source in copies.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        written.append(target)
    if _update_gitignore(folder / ".gitignore"):
        written.append(folder / ".gitignore")

    load_config(folder / "nl2sql.yaml")
    return written


# ---------------------------------------------------------------- the files

TOC_HEADER = """\
# nl2sql.yaml: the table of contents. Each section is in its own file, relative
# to this one. A section can also be written inline here instead.
# See architecture/configuration_v2 in the docs.
"""

EXAMPLE_DATASOURCE = """\
# One datasource per file in this folder. The id is the file name: this one is
# "example". Point it at your database, or rename the file and change the type.
type: sqlite
description: An example SQLite database. Describe what is in yours; the resolver reads it.
connection:
  database: example.db

# A server database keeps its password out of this file, as a reference:
#
# type: postgres
# description: Orders, customers and revenue for the EU shop.
# connection:
#   host: db.internal
#   database: sales
#   user: nl2sql
#   password: "${env:SALES_DB_PASSWORD}"
# limits: { row_limit: 1000, timeout_ms: 8000 }
# sample_questions:
#   - What was revenue by region last quarter?
"""

POLICIES_HEADER = """\
# Who may read what. allow_all lets every caller read everything: fine on your
# own machine, not for a shared deployment. Replace it with roles, for example:
#
# default_role: analyst
# roles:
#   analyst:
#     description: Sales, except the audit schema
#     allow: [example]
#     deny: ["example.audit.*"]
"""

EMBEDDINGS_HEADER = """\
# The embeddings the index is built with. local runs on this machine and needs
# no key.
"""

STORAGE_HEADER = """\
# Where nl2sql keeps its index, state, traces and result files, relative to
# nl2sql.yaml.
"""

CURATED_HEADER = """\
# Verified queries: SQL a person wrote and reviewed, with typed params, and the
# questions each one answers. While this section is present, curated answers
# are on. See docs/configuration/curated.md.
"""

DEMO_SETTINGS_HEADER = """\
# The demo's tuning knobs; every other setting keeps its default. A live run
# makes six model calls, so a question may take 300 s, and every run writes a
# trace for the playground's Debug drill-down. See configuration/settings.
"""


def _llm_file(llm: LLM) -> str:
    from nl2sql.llm.providers import env_var_for_provider

    variable = env_var_for_provider(llm.provider)
    header = (
        "# The LLM every agent uses. No key is written here: without api_key, the\n"
        f"# provider's standard variable, {variable}, is read. To read another one:\n"
        f'#   api_key: "${{env:MY_{variable}}}"\n'
    )
    return header + _dump({"provider": llm.provider, "model": llm.model, "temperature": llm.temperature})


def _datasource_file(datasource: Datasource) -> str:
    """One datasource file: the datasource without its id, which is the file name."""
    return _dump(datasource.model_dump(mode="json", exclude_defaults=True, exclude={"id"}))


def _section_file(section: str) -> str:
    return "datasources/" if section == "datasources" else f"{section}.yaml"


def _packaged_databases() -> List[Path]:
    from nl2sql.datasets import CHINOOK_DB_PATH, SUPPORT_DB_PATH, WEBANALYTICS_DB_PATH

    return [CHINOOK_DB_PATH, SUPPORT_DB_PATH, WEBANALYTICS_DB_PATH]


def _update_gitignore(path: Path) -> bool:
    """Add the lines ``.gitignore`` is missing. Returns whether the file changed."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [line for line in GITIGNORE_LINES if line not in text.splitlines()]
    if not missing:
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "".join(f"{line}\n" for line in missing), encoding="utf-8")
    return True


class _Dumper(yaml.SafeDumper):
    """Writes multi-line strings (the SQL) as ``|`` blocks, so a file reads as written."""


_Dumper.add_representer(
    str,
    lambda dumper, value: dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None),
)


def _dump(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
