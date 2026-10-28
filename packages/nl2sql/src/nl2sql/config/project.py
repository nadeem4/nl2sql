"""A new config folder (``write_project``, ``init_project``), the starter's config (``starter_config``) and the demo's
config (``demo_config``).

``write_project`` writes any data ``Config`` as the split layout ``nl2sql init`` has always written: ``nl2sql.yaml``
and one file per section, ``datasources/<id>.yaml``, ``knowledge.yaml`` and a ``glossary.yaml`` for the ``yaml``
knowledge source. ``init_project`` builds the config and calls it; so will ``nl2sql launch``. ``NL2SQL.demo()``
builds on ``demo_config``, and ``init_project(demo=True)`` writes ``demo_config`` out as files, so the two cannot
drift apart. See docs/architecture/configuration_v2.md, section 10, and docs/architecture/wave_c.md, section 7.

A secret is never written into YAML (``nl2sql.config.writer``): ``init_project`` writes none, and the provider's
standard variable is the fallback.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel

from nl2sql.config.loader import load_config
from nl2sql.config.models import (
    LLM,
    Config,
    Curated,
    Datasource,
    Embeddings,
    EvalsConfig,
    KnowledgeSourceConfig,
    PipelineSettings,
    Policies,
    Role,
    Settings,
    Storage,
    TraceSettings,
)
from nl2sql.config.writer import (
    EnvSecrets,
    check_file_name,
    datasource_data,
    dump_model,
    dump_yaml,
    embeddings_data,
    llm_data,
    secrets_data,
    update_gitignore,
    write_env_values,
)

PathLike = Union[str, "os.PathLike[str]"]

DEMO_STORAGE_PATH = Path(".nl2sql-demo")
STORAGE_PATH = ".nl2sql"
DATA_FOLDER = "data"
EVALS_FOLDER = "evals"
# The suites the package ships; found by path, so building the demo's config loads no evaluation code.
PACKAGED_SUITES = Path(__file__).resolve().parents[1] / "evaluation" / "suites"
GLOSSARY_FILE = "glossary.yaml"
# The sections beyond the six every project has: written when the config sets them.
OPTIONAL_SECTIONS = ("curated", "secrets", "settings", "api", "observers", "hooks", "evals", "conversations",
                     "chat_tools", "dashboards", "mcp")


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
    evals_dir: Optional[PathLike] = None,
    glossary: Optional[PathLike] = None,
) -> Config:
    """The demo: three linked SQLite databases, their roles, verified queries and glossary.

    The databases are opened read-only from ``data_dir``, by default the
    folder the package installed them in. Each datasource carries its sample
    questions and indexes its text columns' values (``index.sample_values``).
    Without ``llm``, the default OpenAI model is used. Its eval suite is
    the Chinook suite in ``evals_dir``, by default the one the package ships.
    Its knowledge is one ``yaml`` source reading ``glossary``, by default the
    demo glossary the package ships.

    Its settings are the demo's own: a live run walks six model calls (one
    OpenAI run took 136 s), so a question may take 300 s; every run writes a
    trace, which the playground's Debug drill-down reads; and a refusal names
    the table, since showing one is the demo's point.
    """
    from nl2sql.datasets import CHINOOK_DB_PATH, DEMO_GLOSSARY_PATH
    from nl2sql.datasets.demo import DEMO_CURATED, DEMO_DATASOURCES, DEMO_POLICIES, DEMO_QUESTIONS_BY_DATASOURCE

    folder = Path(data_dir) if data_dir is not None else CHINOOK_DB_PATH.parent
    datasources = [
        Datasource(**{
            **source,
            "connection": {**source["connection"], "database": (folder / source["connection"]["database"]).as_posix()},
            "sample_questions": DEMO_QUESTIONS_BY_DATASOURCE[source["id"]],
            # Off by default, since values reach the index, the prompts and the
            # provider; the demo's sample data has nothing to keep back.
            "index": {**source.get("index", {}), "sample_values": True},
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
        evals=EvalsConfig(suites=[(Path(evals_dir) if evals_dir is not None else PACKAGED_SUITES) / "chinook.yaml"]),
        knowledge=[KnowledgeSourceConfig(
            type="yaml", path=Path(glossary if glossary is not None else DEMO_GLOSSARY_PATH).as_posix())],
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
    are copied into ``data/``, the eval suites into ``evals/`` and the demo glossary into ``glossary.yaml``;
    otherwise ``glossary.yaml`` holds one empty block for the example datasource. Either way
    ``knowledge.yaml`` names it as the one ``yaml`` knowledge source. A file that already exists is an error
    naming it, and nothing is written, unless ``force`` is set: it deletes the files it would write first.
    ``.gitignore`` is never overwritten: the lines it is missing are added.
    """
    folder = Path(folder)
    llm = default_llm(provider)
    extra: Dict[str, Union[str, Path]]
    if demo:
        from nl2sql.datasets import DEMO_GLOSSARY_PATH

        config = demo_config(llm=llm, path=STORAGE_PATH, data_dir=DATA_FOLDER, evals_dir=EVALS_FOLDER,
                             glossary=GLOSSARY_FILE)
        extra = {GLOSSARY_FILE: DEMO_GLOSSARY_PATH}
        extra.update({f"{DATA_FOLDER}/{source.name}": source for source in _packaged_databases()})
        extra.update({f"{EVALS_FOLDER}/{suite.name}": suite for suite in sorted(PACKAGED_SUITES.glob("*.yaml"))})
    else:
        config = starter_config(llm=llm, path=STORAGE_PATH).with_datasource(Datasource(
            id="example", type="sqlite", connection={"database": "example.db"},
            description="An example SQLite database. Describe what is in yours; the resolver reads it."))
        extra = {"datasources/example.yaml": EXAMPLE_DATASOURCE}
    if force:
        for name in _plan(config, folder, False, extra)[0]:
            (folder / name).unlink(missing_ok=True)
    return write_project(config, folder, extra=extra)


def starter_config(*, llm: LLM, path: PathLike = STORAGE_PATH) -> Config:
    """The config a project starts with: no datasources, ``allow_all`` policies, local embeddings, and one ``yaml``
    knowledge source, ``glossary.yaml`` beside ``path`` (the storage folder). Add datasources with
    ``Config.with_datasource``; ``write_project`` writes it with the glossary."""
    path = Path(path)
    return Config(datasources=[], llm=llm, embeddings=Embeddings(provider="local"), policies=Policies(allow_all=True),
                  storage=Storage(path=path),
                  knowledge=[KnowledgeSourceConfig(type="yaml", path=(path.parent / GLOSSARY_FILE).as_posix())])


def write_project(
    config: Config,
    folder: PathLike,
    *,
    env_file: bool = False,
    extra: Optional[Dict[str, Union[str, Path]]] = None,
) -> List[Path]:
    """Write ``config`` as a new project in ``folder``, load what was written with ``load_config``, and return the files.

    The layout is ``init_project``'s: ``nl2sql.yaml`` and a file per section, ``datasources/<id>.yaml``,
    ``knowledge.yaml`` and an empty ``glossary.yaml`` (one block per datasource) for a ``yaml`` knowledge source
    whose file is not given in ``extra``, and the ``.gitignore`` lines it is missing. A section the config leaves
    at its default is left out. ``extra`` maps a path in ``folder`` to the text to write there, or the file to copy.

    A secret field is written as a reference (D122): a literal becomes ``${env:NL2SQL_<PATH>}`` with its value in
    ``folder/.env`` when ``env_file`` is set, and is refused otherwise.

    Raises:
        FileExistsError: a file it would write exists (it never overwrites). Nothing is written.
        ValueError: a section holds an object, not data (an adapter, store, hook, observer or chat model), or two
            secrets map to one environment variable. Nothing is written.
        SecretValueInConfig: a secret is a literal and ``env_file`` is not set. Nothing is written.
        ConfigError: what was written does not load.
    """
    folder = Path(folder)
    files, env_values, ignore = _plan(config, folder, env_file, extra)
    existing = sorted(name for name in files if (folder / name).exists())
    if existing:
        raise FileExistsError(f"{folder.as_posix()} already has {', '.join(existing)}. write_project never "
                              "overwrites a file.")
    written: List[Path] = []
    for name, content in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, Path):
            shutil.copyfile(content, target)
        else:
            target.write_text(content, encoding="utf-8")
        written.append(target)
    (folder / "datasources").mkdir(exist_ok=True)
    if update_gitignore(folder / ".gitignore", ignore):
        written.append(folder / ".gitignore")
    if env_values:
        written.append(write_env_values(folder, env_values))
    load_config(folder / "nl2sql.yaml")
    return written


def _plan(config: Config, folder: Path, env_file: bool,
          extra: Optional[Dict[str, Union[str, Path]]]) -> Tuple[Dict[str, Union[str, Path]], Dict[str, str], List[str]]:
    """The files ``write_project`` would write (path to text or source), the ``.env`` values, and the ``.gitignore`` lines."""
    secrets = EnvSecrets(env_file=env_file)
    files: Dict[str, Union[str, Path]] = {}
    for datasource in config.datasources:
        files[f"datasources/{check_file_name(datasource.id)}"] = _dump(datasource_data(datasource, secrets))
    llm = config.llm
    llm_dump = dump_model(llm, "The llm section")
    files["llm.yaml"] = _llm_header(llm) + _dump(llm_data(
        {"provider": llm.provider, "model": llm.model, "temperature": llm.temperature, **llm_dump}, secrets))
    files["embeddings.yaml"] = EMBEDDINGS_HEADER + _dump(embeddings_data(
        dump_model(config.embeddings, "The embeddings section"), secrets))
    policies = dump_model(config.policies, "The policies section")
    files["policies.yaml"] = (POLICIES_HEADER if config.policies.allow_all else "") + _dump(policies)
    storage = dump_model(config.storage, "The storage section")
    storage["path"] = _relative(config.storage.path, folder)
    files["storage.yaml"] = STORAGE_HEADER + _dump(storage)
    knowledge = [_entry(entry, "The knowledge section", folder) for entry in config.knowledge]
    files["knowledge.yaml"] = KNOWLEDGE_HEADER + _dump(knowledge)
    sections = ["datasources", "llm", "embeddings", "policies", "storage", "knowledge"]
    for section in OPTIONAL_SECTIONS:
        data = _optional(config, section, secrets)
        if data is not None:
            files[f"{section}.yaml"] = _SECTION_HEADERS.get(section, "") + _dump(data)
            sections.append(section)
    files["nl2sql.yaml"] = TOC_HEADER + "version: 2\n" + "".join(
        f"{section + ':':<13}{{ file: {_section_file(section)} }}\n" for section in sections)

    ids = [d.id for d in config.datasources]
    for source in knowledge:
        name = source.get("path") if source.get("type") == "yaml" else None
        if isinstance(name, str) and not Path(name).is_absolute() and not name.startswith(".."):
            files.setdefault(name, _empty_glossary(ids))
    files.update(extra or {})
    ignore = [f"{storage['path']}/"] if not Path(storage["path"]).is_absolute() else []
    return files, secrets.values, [*ignore, ".env"]


def _optional(config: Config, section: str, secrets: EnvSecrets) -> Any:
    """A section the config may leave out, as data; None when it is at its default (``curated`` when it is absent)."""
    value = getattr(config, section)
    what = f"The {section} section"
    if value is None:
        return None
    if isinstance(value, list):
        if not value and section != "observers":  # observers: [] means none, not the default observers
            return None
        return [_entry(item, what, None) if isinstance(item, BaseModel) else _plain(item, what) for item in value]
    data = dump_model(value, what)
    if section == "secrets":
        data = secrets_data(data, secrets)
    return data if data or section == "curated" else None


def _entry(item: Any, what: str, folder: Optional[Path]) -> Dict[str, Any]:
    """One list item as data; a knowledge source's file is written relative to the project when it is inside it."""
    if not isinstance(item, BaseModel):
        raise ValueError(f"{what} holds an object, not data. write_project writes data only.")
    data = dump_model(item, what)
    if folder is not None and data.get("type") == "yaml" and isinstance(data.get("path"), str):
        data["path"] = _relative(Path(data["path"]), folder)
    return data


def _plain(item: Any, what: str) -> Any:
    if isinstance(item, (str, int, float, bool)):
        return item
    raise ValueError(f"{what} holds an object, not data. write_project writes data only.")


def _relative(path: Path, folder: Path) -> str:
    """``path`` as the files write it: relative to ``folder`` when it is inside it, else as given (posix)."""
    if path.is_absolute():
        try:
            return path.relative_to(folder.absolute()).as_posix()
        except ValueError:
            pass
    return path.as_posix()


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

EVALS_HEADER = """\
# The eval suites engine.evals.run runs, relative to nl2sql.yaml, and how they are
# scored. evals/chinook.yaml is the Chinook gold suite. evals/routing.yaml asks
# which database a question belongs to; run it by name, in mode retrieval or full:
# nl2sql eval run --mode retrieval --suite evals/routing.yaml. evals/clarify.yaml holds
# questions the glossary leaves open; run it with engine.evals.run(..., clarify=True).
# See the Evals docs.
"""

KNOWLEDGE_HEADER = """\
# The knowledge sources, in precedence order: the first that sets a description
# or a role wins. glossary.yaml is where people describe tables, columns, joins,
# business terms and rules. See docs/configuration/knowledge.md.
"""

SETTINGS_HEADER = """\
# The tuning knobs; every other setting keeps its default. See configuration/settings.
"""

_SECTION_HEADERS = {"curated": CURATED_HEADER, "evals": EVALS_HEADER, "settings": SETTINGS_HEADER}


def _llm_header(llm: LLM) -> str:
    """The comment on top of ``llm.yaml``: which variable the key is read from when ``api_key`` is not set."""
    from nl2sql.llm.providers import env_var_for_provider

    if llm.api_key is not None or not isinstance(llm.provider, str):
        return ""
    variable = env_var_for_provider(llm.provider)
    return (
        "# The LLM every agent uses. No key is written here: without api_key, the\n"
        f"# provider's standard variable, {variable}, is read. To read another one:\n"
        f'#   api_key: "${{env:MY_{variable}}}"\n'
    )


def _empty_glossary(datasource_ids: List[str]) -> str:
    """A glossary with one empty block per datasource, in the canonical form the engine writes."""
    from nl2sql.knowledge.yaml_source import canonical_yaml
    from nl2sql_sdk.knowledge import Glossary

    return canonical_yaml(Glossary(version=1, datasources={ds_id: {} for ds_id in datasource_ids}))


def _section_file(section: str) -> str:
    return "datasources/" if section == "datasources" else f"{section}.yaml"


def _packaged_databases() -> List[Path]:
    from nl2sql.datasets import CHINOOK_DB_PATH, SUPPORT_DB_PATH, WEBANALYTICS_DB_PATH

    return [CHINOOK_DB_PATH, SUPPORT_DB_PATH, WEBANALYTICS_DB_PATH]


_dump = dump_yaml
